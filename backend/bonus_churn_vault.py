"""Append-only, owner-scoped churn evidence on a persistent Railway volume.

No R2 calls, account writes, journal access, deletion, retention purge or restores.
Payloads are AES-GCM encrypted, content addressed and verified after commit.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from bonus_backups import owner
from bonus_terms_archive import FIELDS as TERMS_FIELDS, STRING_FIELDS, _safe_structure

router = APIRouter()
MAX_BYTES = 1024 * 1024
SHA = re.compile(r'^[0-9a-f]{64}$')
FIELDS = TERMS_FIELDS | frozenset(('closed', 'bonusRecd', 'reqMet', 'termsOrigin', 'analyzedTC'))
DATES = ('opened', 'closed', 'bonusRecd', 'reqMet', 'couponEnrollmentDate', 'offerEnrollmentDate')


def validate(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode()) <= MAX_BYTES:
        raise ValueError('Invalid record size')
    data = json.loads(raw)
    if not isinstance(data, dict) or not set(data).issubset(FIELDS):
        raise ValueError('Unsupported record fields')
    if data.get('format') != 'BonusTrackerChurnRecord' or type(data.get('version')) is not int or data.get('version') != 1:
        raise ValueError('Unsupported record format')
    for field, limit in (('bank', 160), ('entryId', 160)):
        if not isinstance(data.get(field), str) or not 1 <= len(data[field].strip()) <= limit:
            raise ValueError('Record identity required')
    if data.get('accountType') not in ('personal', 'business'):
        raise ValueError('Account audience required')
    for field in DATES:
        value = data.get(field, '')
        if not isinstance(value, str):
            raise ValueError('Date must be text')
        if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or date.fromisoformat(value).isoformat() != value):
            raise ValueError('Invalid event date')
    for field in (STRING_FIELDS - {'format', 'version'}) | {'closed', 'bonusRecd', 'reqMet', 'analyzedTC', 'termsOrigin'}:
        if field in data and not isinstance(data[field], str):
            raise ValueError('Invalid archive text')
    for field in ('bonus', 'schemaVersion', 'minHoldDays', 'earlyCloseFee', 'churnPeriodValue', 'closeBufferDays', 'closeFeeCountdownDays'):
        value = data.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 100000000):
            raise ValueError('Invalid archive number')
    for field, value in data.items():
        if isinstance(value, str) and len(value) > 600000:
            raise ValueError('Record text too large')
    # Existing field-name restrictions keep credentials, account numbers and
    # transaction/balance data out of this narrow archive.
    _safe_structure(data)
    return data


def root():
    mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '')
    if not mount or Path(mount).resolve() != Path('/var/lib/bonus-churn-vault') or not Path(mount).is_dir():
        raise HTTPException(503, 'Independent history storage is not provisioned.')
    return Path(mount)


def _key(directory):
    key_path = directory / '.vault-key'
    if not key_path.exists():
        if (directory / 'churn.sqlite3').exists():
            raise ValueError('Archive encryption key unavailable')
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as file:
                file.write(os.urandom(32)); file.flush(); os.fsync(file.fileno())
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    key = key_path.read_bytes()
    if len(key) != 32:
        raise ValueError('Archive encryption key unavailable')
    return key


def connection(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cipher = AESGCM(_key(directory))
    path = directory / 'churn.sqlite3'
    db = sqlite3.connect(path, timeout=15)
    os.chmod(path, 0o600)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS records (owner TEXT NOT NULL, digest TEXT NOT NULL, created TEXT NOT NULL, nonce BLOB NOT NULL, body BLOB NOT NULL, PRIMARY KEY(owner,digest))')
    db.commit()
    return db, cipher


def _digest(uid, digest):
    if not SHA.fullmatch(str(uid)) or not SHA.fullmatch(str(digest)):
        raise HTTPException(400, 'Invalid archive identity.')


def _read(db, cipher, uid, digest):
    _digest(uid, digest)
    row = db.execute('SELECT created,nonce,body FROM records WHERE owner=? AND digest=?', (uid, digest)).fetchone()
    if row is None:
        raise HTTPException(404, 'This archived record was not found.')
    raw = cipher.decrypt(row[1], row[2], (uid + ':' + digest).encode()).decode()
    if hashlib.sha256(raw.encode()).hexdigest() != digest:
        raise ValueError('Archive integrity failed')
    data = validate(raw)
    missing = [field for field in ('opened', 'closed', 'bonusRecd', 'tcSourceRaw') if not data.get(field)]
    return {'id': digest, 'sha256': digest, 'verified': True, 'createdAt': row[0],
            'storage': 'independent-volume', 'immutable': True, 'missingFields': missing,
            'bank': data['bank'], 'accountType': data['accountType'], 'entryId': data['entryId'],
            'opened': data.get('opened', ''), 'closed': data.get('closed', ''),
            'bonusRecd': data.get('bonusRecd', ''), 'snapshot': raw}


def save(directory, uid, packet):
    if not isinstance(packet, dict) or set(packet) != {'snapshot', 'sha256'}:
        raise ValueError('Unsupported upload')
    raw = packet['snapshot']; validate(raw)
    digest = hashlib.sha256(raw.encode()).hexdigest(); _digest(uid, digest)
    if packet['sha256'] != digest:
        raise ValueError('Record checksum mismatch')
    db, cipher = connection(directory)
    try:
        nonce = os.urandom(12)
        encrypted = cipher.encrypt(nonce, raw.encode(), (uid + ':' + digest).encode())
        with db:
            db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?,?,?)',
                       (uid, digest, datetime.now(timezone.utc).isoformat(), nonce, encrypted))
    finally:
        db.close()
    # Reopen from durable storage, decrypt and hash before acknowledging success.
    result = read(directory, uid, digest)
    return {key: value for key, value in result.items() if key != 'snapshot'}


def read(directory, uid, digest):
    _digest(uid, digest)
    db, cipher = connection(directory)
    try:
        return _read(db, cipher, uid, digest)
    finally:
        db.close()


def catalog(directory, uid, offset=0):
    _digest(uid, '0' * 64)
    db, cipher = connection(directory)
    try:
        total = db.execute('SELECT count(*) FROM records WHERE owner=?', (uid,)).fetchone()[0]
        ids = db.execute('SELECT digest FROM records WHERE owner=? ORDER BY created DESC,digest LIMIT 50 OFFSET ?', (uid, offset)).fetchall()
        rows = [{k: v for k, v in _read(db, cipher, uid, row[0]).items() if k != 'snapshot'} for row in ids]
        return {'records': rows, 'total': total, 'nextOffset': offset + 50 if offset + 50 < total else None,
                'storage': 'independent-volume', 'immutable': True}
    finally:
        db.close()


async def operation(fn, *args):
    try:
        return await run_in_threadpool(fn, root(), *args)
    except HTTPException:
        raise
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Churn history validation or integrity verification failed.')
    except Exception:
        raise HTTPException(503, 'Independent churn history is unavailable; local records are retained.')


@router.post('/api/bonus-tracker/churn-vault')
async def put_record(request: Request):
    uid = owner(request)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BYTES * 2 + 4096:
            raise HTTPException(413, 'Churn history upload too large.')
    try:
        packet = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Invalid churn history upload.')
    return JSONResponse(await operation(save, uid, packet), headers={'Cache-Control': 'no-store'})


@router.get('/api/bonus-tracker/churn-vault')
async def list_records(request: Request, offset: int = Query(0, ge=0, le=100000)):
    return JSONResponse(await operation(catalog, owner(request), offset), headers={'Cache-Control': 'no-store'})


@router.get('/api/bonus-tracker/churn-vault/{digest}')
async def get_record(digest: str, request: Request):
    return JSONResponse(await operation(read, owner(request), digest), headers={'Cache-Control': 'no-store'})

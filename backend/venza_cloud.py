"""Private Venza state, using the existing journal R2 connection and owner auth."""
import hashlib
import json
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from botocore.exceptions import ClientError
from bonus_backups import owner, store, operation

router = APIRouter()
MAX_BYTES = 12 * 1024 * 1024
KEYS = frozenset(('vz_log','vz_mi','vz_inventory','vz_master_parts','vz_mi_state_v1',
    'vz_mi_history_v1','vz_oil_avg_reset_at','vz_avg_service_type','vz_schedule_overrides',
    'vz_service_part_requirements','vz_master_parts_inventory_only_v1','vz_verification_overrides',
    'vz_basic_parts'))
DYNAMIC_KEY = re.compile(r'^vz_(?:photo_|part_photo_|migration_)[a-zA-Z0-9_-]{1,160}$')


def validate_snapshot(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode()) <= MAX_BYTES:
        raise ValueError('Invalid state size')
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'format','version','storageSnapshot'}:
        raise ValueError('Unsupported state fields')
    if data['format'] != 'VenzaCloudState' or type(data['version']) is not int or data['version'] != 1:
        raise ValueError('Unsupported state version')
    snap = data['storageSnapshot']
    if not isinstance(snap, dict) or 'vz_log' not in snap or len(snap) > 20000:
        raise ValueError('Missing service records')
    for key, value in snap.items():
        if not isinstance(key,str) or not (key in KEYS or DYNAMIC_KEY.fullmatch(key)) or not isinstance(value,str):
            raise ValueError('Unsupported storage key or value')
        if key.startswith(('vz_photo_', 'vz_part_photo_')) and not value.startswith(('data:image/jpeg;base64,','data:image/png;base64,','data:image/webp;base64,','data:image/gif;base64,')):
            raise ValueError('Unsupported receipt photo')
    records = json.loads(snap['vz_log'])
    if not isinstance(records,list) or len(records) > 10000 or any(not isinstance(x,dict) for x in records):
        raise ValueError('Invalid service records')
    if 'vz_mi' in snap and not re.fullmatch(r'\d{1,7}', snap['vz_mi']):
        raise ValueError('Invalid mileage')
    # Reject malformed JSON before any live phone receives it.
    for key in KEYS - {'vz_mi','vz_oil_avg_reset_at','vz_avg_service_type','vz_master_parts_inventory_only_v1'}:
        if key in snap:
            parsed = json.loads(snap[key])
            if not isinstance(parsed,(list,dict)):
                raise ValueError('Invalid stored JSON')
    return data


def state_key(uid):
    return f'venza-garage/v1/{uid}/current.json'


def read_state(storage, uid):
    try:
        obj = storage.client.get_object(Bucket=storage.bucket, Key=state_key(uid))
    except ClientError as exc:
        if str(exc.response.get('Error',{}).get('Code')) in ('404','NoSuchKey','NotFound'):
            return {'exists':False,'revision':None}
        raise
    if obj.get('ContentLength',0) > MAX_BYTES:
        raise ValueError('Invalid stored state size')
    body = obj['Body']
    try:
        raw = body.read(MAX_BYTES + 1)
    finally:
        body.close()
    if len(raw) > MAX_BYTES:
        raise ValueError('Invalid stored state size')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != obj.get('Metadata',{}).get('sha256'):
        raise ValueError('Cloud integrity check failed')
    text = raw.decode()
    validate_snapshot(text)
    return {'exists':True,'revision':obj['ETag'],'snapshot':text,'sha256':digest,
            'savedAt':obj.get('Metadata',{}).get('saved-at'),'verified':True}


def save_state(storage,uid,packet):
    if not isinstance(packet,dict) or set(packet) != {'snapshot','sha256','baseRevision'}:
        raise ValueError('Unsupported upload')
    validate_snapshot(packet['snapshot'])
    raw = packet['snapshot'].encode()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != packet['sha256']:
        raise ValueError('Upload checksum mismatch')
    revision = packet['baseRevision']
    if revision is not None and (not isinstance(revision,str) or not re.fullmatch(r'"[a-fA-F0-9-]{1,100}"',revision)):
        raise ValueError('Invalid revision')
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = storage.client.put_object(Bucket=storage.bucket,Key=state_key(uid),Body=raw,
            ContentType='application/json',Metadata={'sha256':digest,'saved-at':now},
            **({'IfMatch':revision} if revision else {'IfNoneMatch':'*'}))
    except ClientError as exc:
        if exc.response.get('ResponseMetadata',{}).get('HTTPStatusCode') in (409,412):
            raise HTTPException(409,'Another device saved newer records. Review the cloud copy before replacing it.')
        raise
    verified = read_state(storage,uid)
    if verified.get('sha256') != digest or verified.get('revision') != result['ETag']:
        raise HTTPException(409,'Cloud records changed during save. Local records are preserved.')
    return {k:verified[k] for k in ('revision','sha256','savedAt','verified')}


@router.get('/api/venza/state')
async def get_state(request:Request):
    uid = owner(request)
    return JSONResponse(await operation(read_state,store(),uid),headers={'Cache-Control':'no-store'})


@router.put('/api/venza/state')
async def put_state(request:Request):
    uid = owner(request)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_BYTES*2 + 4096:
            raise HTTPException(413,'Venza cloud upload is too large.')
    try:
        packet = json.loads(raw)
    except (ValueError,UnicodeError):
        raise HTTPException(400,'Invalid Venza cloud upload.')
    return JSONResponse(await operation(save_state,store(),uid,packet),headers={'Cache-Control':'no-store'})

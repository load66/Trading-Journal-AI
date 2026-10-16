"""Owner-private SimpleFIN trial. Evidence only; never rewrites tracker milestones."""
import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, unquote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from botocore.exceptions import ClientError
from bonus_backups import owner, store

def private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'


from fastapi import Depends
router = APIRouter(prefix='/api/bonus-tracker/simplefin', dependencies=[Depends(private_response)])
DAY = 86400
MAX_STATE = 8 * 1024 * 1024
HOSTS = {'bridge.simplefin.org', 'beta-bridge.simplefin.org'}


def cipher():
    try:
        return Fernet(os.environ['SIMPLEFIN_ENCRYPTION_KEY'].encode())
    except (KeyError, ValueError):
        raise HTTPException(503, 'Bank syncing is awaiting secure server setup.')


def key(uid):
    return f'bonus-tracker-simplefin/v1/{uid}/state.enc'


def read_state(uid):
    s = store()
    try:
        r = s.client.get_object(Bucket=s.bucket, Key=key(uid))
    except ClientError as e:
        if e.response['Error']['Code'] in ('NoSuchKey', '404'):
            return {'version': 1, 'accounts': [], 'mappings': {}, 'transactions': {}}, None
        raise HTTPException(503, 'Private bank-sync storage is temporarily unavailable.') from None
    raw = r['Body'].read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise HTTPException(503, 'Private bank-sync storage needs review.')
    try:
        return json.loads(cipher().decrypt(raw)), r['ETag']
    except (InvalidToken, ValueError):
        raise HTTPException(503, 'Bank-sync storage could not be decrypted. Do not reconnect yet.') from None


def write_state(uid, state, etag):
    raw = cipher().encrypt(json.dumps(state, sort_keys=True).encode())
    if len(raw) > MAX_STATE:
        raise HTTPException(413, 'Bank-sync history exceeded its safe storage limit.')
    s = store()
    args = {'Bucket': s.bucket, 'Key': key(uid), 'Body': raw, 'ContentType': 'application/octet-stream',
            **({'IfMatch': etag} if etag else {'IfNoneMatch': '*'})}
    try:
        s.client.put_object(**args)
    except ClientError as e:
        if e.response['Error']['Code'] in ('PreconditionFailed', 'ConditionalRequestConflict', '412', '409'):
            raise HTTPException(409, 'Bank syncing is busy. Refresh and try again.') from None
        raise HTTPException(503, 'Bank-sync changes could not be saved. Try again.') from None
    saved, new_etag = read_state(uid)
    if saved != state:
        raise HTTPException(409, 'Bank-sync changes need a fresh read. Refresh and try again.')
    return new_etag


def safe_url(raw, claim=False):
    """No arbitrary URLs, redirects, custom ports, fragments or credential forwarding."""
    try:
        p = urlsplit(raw)
        if (p.scheme != 'https' or p.hostname not in HOSTS or p.port not in (None, 443)
                or p.query or p.fragment or '\\' in raw or any(ord(c) < 33 for c in raw)):
            raise ValueError()
        if claim:
            if p.username or p.password or not re.fullmatch(r'/simplefin/claim/[A-Za-z0-9_-]+', p.path):
                raise ValueError()
        elif not p.username or not p.password or p.path.rstrip('/') != '/simplefin':
            raise ValueError()
        return p
    except ValueError:
        raise HTTPException(400, 'Use a valid SimpleFIN Bridge setup token.') from None


def claim_url(token):
    try:
        raw = base64.b64decode(token.strip(), validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        raise HTTPException(400, 'Use a valid SimpleFIN Bridge setup token.') from None
    safe_url(raw, claim=True)
    return raw


def network(url, method='GET', auth=None, params=None):
    try:
        with httpx.Client(timeout=25, follow_redirects=False) as c:
            with c.stream(method, url, auth=auth, params=params) as r:
                if r.status_code == 402:
                    raise HTTPException(402, 'SimpleFIN requires subscription attention. No upgrade was made.')
                if r.status_code in (401, 403):
                    raise HTTPException(403, 'SimpleFIN access was rejected. If connecting, disable this setup token in SimpleFIN and create a new one; it may have already been claimed.')
                if r.status_code != 200:
                    raise HTTPException(502, 'SimpleFIN could not complete the request. Try again later.')
                chunks, size = [], 0
                for b in r.iter_bytes():
                    size += len(b)
                    if size > MAX_STATE:
                        raise HTTPException(502, 'SimpleFIN returned more data than this trial supports.')
                    chunks.append(b)
                return b''.join(chunks)
    except httpx.HTTPError:
        raise HTTPException(502, 'SimpleFIN could not be reached. Try again later.') from None


def get_data(access, selected=None, balances=False):
    p = safe_url(access)
    endpoint = urlunsplit(('https', p.hostname, p.path.rstrip('/') + '/accounts', '', ''))
    now = int(time.time())
    params = [('version', '2'), ('start-date', str(now - 30 * DAY)), ('end-date', str(now + 1))]
    if balances:
        params.append(('balances-only', '1'))
    else:
        params.extend(('account', x['id']) for x in selected)
    raw = network(endpoint, auth=(unquote(p.username), unquote(p.password)), params=params)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('accounts'), list):
            raise ValueError()
        if not isinstance(data.get('connections', []), list) or not isinstance(data.get('errlist', []), list) or not isinstance(data.get('errors', []), list):
            raise ValueError()
        if len(data['accounts']) > 500:
            raise ValueError()
        for a in data['accounts']:
            if (not isinstance(a, dict) or not isinstance(a.get('transactions', []), list)
                    or len(a.get('transactions', [])) > 20000 or not isinstance(a.get('org', {}), dict)):
                raise ValueError()
        return data
    except ValueError:
        raise HTTPException(502, 'SimpleFIN returned an unsupported response.') from None


def clean(value, limit=300):
    value = str(value or '')
    value = re.sub(r'https?://\S+', '[link removed]', value)
    return ''.join(c for c in value if ord(c) >= 32)[:limit]


def account_key(a):
    # v2 account IDs are unique only within their connection.
    return hashlib.sha256((str(a.get('conn_id', '')) + '\0' + str(a.get('id', ''))).encode()).hexdigest()


def normalize_accounts(data):
    conns = {str(c.get('conn_id')): c for c in data.get('connections', []) if isinstance(c, dict)}
    result = []
    for a in data['accounts']:
        if not isinstance(a, dict) or not isinstance(a.get('id'), str) or not a['id']:
            raise HTTPException(502, 'SimpleFIN returned an account without a stable identity.')
        c = conns.get(str(a.get('conn_id')), {})
        result.append({'key': account_key(a), 'id': a['id'], 'connection': str(a.get('conn_id', '')),
                       'name': clean(a.get('name')), 'bank': clean(c.get('name') or a.get('conn_name') or a.get('org', {}).get('name')),
                       'currency': clean(a.get('currency'), 40), 'balance': clean(a.get('balance'), 40),
                       'balanceDate': a.get('balance-date') if isinstance(a.get('balance-date'), (int, float)) else None})
    if len({a['key'] for a in result}) != len(result):
        raise HTTPException(502, 'SimpleFIN returned conflicting account identities.')
    return result


def money(value):
    from decimal import Decimal, InvalidOperation
    try:
        n = Decimal(str(value))
        if not n.is_finite() or abs(n) > 10**12:
            raise ValueError()
        return str(n)
    except (InvalidOperation, ValueError):
        raise HTTPException(502, 'SimpleFIN returned an invalid transaction amount.') from None


def classify(amount, description):
    from decimal import Decimal
    n = Decimal(amount)
    text = description.lower()
    if n < 0 and re.search(r'bonus|promotional', text) and re.search(r'revers|clawback|adjust', text):
        return 'possible_reversal'
    if n <= 0:
        return 'withdrawal'
    if re.search(r'payroll|salary|refund|interest|dividend|transfer|cashback|cash back', text):
        return 'other_deposit'
    if re.search(r'\bbonus\b|promotional (?:credit|payment|reward)', text):
        return 'possible_bonus'
    return 'deposit'


def merge(state, data, now):
    fresh = normalize_accounts(data)
    fresh_by_key = {a['key']: a for a in fresh}
    old_by_key = {a['key']: a for a in state['accounts']}
    state['accounts'] = [fresh_by_key.get(k, a) for k, a in old_by_key.items()]
    state['missingAccounts'] = [k for k in state['mappings'] if k not in fresh_by_key]
    warnings = [clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)]
    warnings += [clean(e) for e in data.get('errors', []) if isinstance(e, str)]
    state['warnings'] = warnings[:50]
    for a in data['accounts']:
        k = account_key(a)
        if k not in state['mappings']:
            continue
        rows = copy.deepcopy(state['transactions'].get(k, {}))
        for t in a.get('transactions', []):
            if not isinstance(t, dict) or t.get('pending') is True or not isinstance(t.get('posted'), (int, float)) or not 0 < t['posted'] <= now + 300:
                continue
            if not isinstance(t.get('id'), str) or not t['id']:
                raise HTTPException(502, 'SimpleFIN returned a transaction without a stable identity.')
            amount = money(t.get('amount'))
            desc = clean(t.get('description'), 500)
            row = {'id': t['id'], 'posted': t['posted'], 'amount': amount, 'description': desc,
                   'kind': classify(amount, desc), 'observedAt': now}
            prior = rows.get(t['id'])
            if prior:
                row['observedAt'] = prior['observedAt']
                if any(prior.get(f) != row[f] for f in ('posted', 'amount', 'description')):
                    row['changedAt'] = now
                elif prior.get('changedAt'):
                    row['changedAt'] = prior['changedAt']
            rows[t['id']] = row
        # Missing rows never imply reversal/deletion; preserve 90 days of observed history.
        state['transactions'][k] = {i: r for i, r in rows.items() if r['posted'] >= now - 90 * DAY}
    state['lastSuccess'] = now
    state['windowStart'] = now - 30 * DAY
    state['windowEnd'] = now
    state['status'] = 'attention' if warnings or state['missingAccounts'] else 'connected'
    return state


def public_state(s):
    # Never serialize the provider credential, setup token, or storage key.
    fields = ('accounts', 'mappings', 'transactions', 'warnings', 'missingAccounts', 'lastSuccess',
              'lastAttempt', 'windowStart', 'windowEnd', 'status', 'nextSyncAt', 'error')
    out = {k: s[k] for k in fields if k in s}
    out.update(connected=bool(s.get('access')), configured=True, evidenceOnly=True)
    return out


def reserve(uid, operation):
    s, tag = read_state(uid)
    now = int(time.time())
    if s.get('leaseUntil', 0) > now:
        raise HTTPException(409, 'Bank syncing is busy. Refresh and try again.')
    s['lease'] = uuid.uuid4().hex
    s['leaseUntil'] = now + 300
    s['operation'] = operation
    return s, write_state(uid, s, tag)


def finish(uid, s, tag):
    s.pop('lease', None)
    s.pop('leaseUntil', None)
    s.pop('operation', None)
    write_state(uid, s, tag)
    return public_state(s)


def sync(uid):
    s, tag = reserve(uid, 'sync')
    if not s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'Connect SimpleFIN first.')
    now = int(time.time())
    if now < s.get('nextSyncAt', 0):
        return finish(uid, s, tag)
    selected = [a for a in s['accounts'] if a['key'] in s['mappings']]
    if not selected:
        return finish(uid, s, tag)
    s['lastAttempt'] = now
    # Reserve the next attempt before network I/O. Restart/timeout cannot exhaust provider quotas.
    s['nextSyncAt'] = now + DAY
    tag = write_state(uid, s, tag)
    try:
        candidate = merge(copy.deepcopy(s), get_data(s['access'], selected), now)
        s = candidate
        s.pop('error', None)
    except HTTPException as e:
        s['status'] = 'attention'
        s['error'] = e.detail
    return finish(uid, s, tag)


class ConnectBody(BaseModel):
    setup_token: str = Field(min_length=16, max_length=4096, repr=False)


class Mapping(BaseModel):
    entryId: str = Field(min_length=1, max_length=120)
    bank: str = Field(min_length=1, max_length=120)
    opened: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')


    @field_validator('opened')
    @classmethod
    def valid_opened(cls, v):
        from datetime import date
        date.fromisoformat(v)
        return v


class SelectionBody(BaseModel):
    mappings: dict[str, Mapping] = Field(max_length=50)


@router.get('')
def status(request: Request):
    uid = owner(request)
    try:
        cipher()
    except HTTPException:
        return {'configured': False, 'connected': False, 'evidenceOnly': True}
    return public_state(read_state(uid)[0])


@router.post('/connect')
def connect(body: ConnectBody, request: Request):
    uid = owner(request)
    cipher()
    url = claim_url(body.setup_token)
    s, tag = reserve(uid, 'connect')
    if s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'A SimpleFIN connection already exists. Disconnect it before replacing it.')
    try:
        access = network(url, method='POST').decode().strip()
        safe_url(access)
        s['access'] = access
        s['status'] = 'connected'
        # Persist a consumed token's credential before attempting account discovery.
        tag = write_state(uid, s, tag)
        s['nextDiscoveryAt'] = int(time.time()) + DAY
        data = get_data(access, balances=True)
        s['accounts'] = normalize_accounts(data)
        s['warnings'] = ([clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)] +
                         [clean(e) for e in data.get('errors', []) if isinstance(e, str)])[:50]
        if s['warnings']:
            s['status'] = 'attention'
    except (HTTPException, UnicodeError) as e:
        s['error'] = e.detail if isinstance(e, HTTPException) else 'SimpleFIN returned an invalid response.'
        s['status'] = 'attention'
    return finish(uid, s, tag)


@router.post('/discover')
def discover(request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'discover')
    now = int(time.time())
    if not s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'Connect SimpleFIN first.')
    if now < s.get('nextDiscoveryAt', 0):
        return finish(uid, s, tag)
    s['nextDiscoveryAt'] = now + DAY
    tag = write_state(uid, s, tag)
    try:
        data = get_data(s['access'], balances=True)
        fresh = normalize_accounts(data)
        s['missingAccounts'] = [k for k in s['mappings'] if k not in {a['key'] for a in fresh}]
        current = {a['key']: a for a in fresh}
        # Preserve missing matched accounts so an omitted response cannot silently delete a match.
        for a in s['accounts']:
            if a['key'] in s['mappings'] and a['key'] not in current:
                fresh.append(a)
        s['accounts'] = fresh
        s['warnings'] = ([clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)] +
                         [clean(e) for e in data.get('errors', []) if isinstance(e, str)])[:50]
        s['status'] = 'attention' if s['warnings'] or s.get('missingAccounts') else 'connected'
        s.pop('error', None)
    except HTTPException as e:
        s['error'] = e.detail
        s['status'] = 'attention'
    return finish(uid, s, tag)


@router.put('/selection')
def select(body: SelectionBody, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'selection')
    allowed = {a['key'] for a in s['accounts']}
    if not s.get('access') or not set(body.mappings).issubset(allowed):
        finish(uid, s, tag)
        raise HTTPException(400, 'Choose accounts from the current connection.')
    mappings = {k: v.model_dump() for k, v in body.mappings.items()}
    # Multiple accounts (checking/savings) can belong to one cycle. One account cannot belong to two.
    s['transactions'] = {k: v for k, v in s['transactions'].items()
                         if k in mappings and s['mappings'].get(k) == mappings[k]}
    s['mappings'] = mappings
    return finish(uid, s, tag)


@router.post('/sync')
def sync_endpoint(request: Request):
    return sync(owner(request))


@router.delete('')
def disconnect(request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'disconnect')
    # Tombstone instead of delete so concurrent stale writes cannot recreate credentials.
    return finish(uid, {'version': 1, 'accounts': [], 'mappings': {}, 'transactions': {}, 'status': 'disconnected'}, tag)


async def scheduled_simplefin_loop():
    while True:
        try:
            if os.getenv('SIMPLEFIN_ENCRYPTION_KEY') and os.getenv('AUTHORIZED_USER_ID'):
                uid = hashlib.sha256(os.environ['AUTHORIZED_USER_ID'].strip().encode()).hexdigest()
                s, _ = await run_in_threadpool(read_state, uid)
                if s.get('access') and s.get('mappings') and time.time() >= s.get('nextSyncAt', 0):
                    await run_in_threadpool(sync, uid)
        except Exception:
            # No financial data, credentials or provider URLs in logs; per-owner status shows errors.
            pass
        await asyncio.sleep(61)

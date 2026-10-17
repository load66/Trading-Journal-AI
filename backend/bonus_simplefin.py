"""Owner-private SimpleFIN trial. Evidence only; never rewrites tracker milestones."""
import asyncio
import base64
import copy
import hashlib
import json
import logging
import os
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit, unquote

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from botocore.exceptions import ClientError
from bonus_backups import owner, store
from finance_category_rules import category_for, micro_deposit_refs
from finance_ai_categories import classify_unknown, cache_key

def private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'


from fastapi import Depends
router = APIRouter(prefix='/api/bonus-tracker/simplefin', dependencies=[Depends(private_response)])
BACKGROUND_LOGGER = logging.getLogger(__name__)

DAY = 86400
MAX_STATE = 8 * 1024 * 1024
HOSTS = {'bridge.simplefin.org', 'beta-bridge.simplefin.org'}
ACCOUNT_KINDS = frozenset(('unclassified', 'checking', 'savings', 'credit_card', 'brokerage',
                           'retirement', 'loan', 'mortgage', 'other_asset', 'other_liability'))
BALANCE_MODES = frozenset(('exclude', 'asset', 'liability'))
TRANSACTION_CATEGORIES = frozenset(('unreviewed', 'income', 'housing', 'utilities', 'groceries',
                                    'dining', 'transportation', 'shopping', 'health', 'entertainment',
                                    'fees', 'taxes', 'travel', 'subscription', 'transfer', 'refund',
                                    'bonus', 'verification', 'loan', 'other'))


def empty_state():
    return {'version': 2, 'accounts': [], 'mappings': {}, 'monitoredAccounts': [],
            'transactions': {}, 'accountProfiles': {}, 'transactionReviews': {},
            'balanceHistory': {}, 'evidenceReviews': {}, 'audit': []}


def migrate_state(value):
    if not isinstance(value, dict) or not isinstance(value.get('accounts', []), list):
        raise HTTPException(503, 'Private financial data needs review before it can be used.')
    state = value
    for field in ('mappings', 'transactions', 'accountProfiles', 'transactionReviews', 'balanceHistory', 'evidenceReviews'):
        if not isinstance(state.get(field, {}), dict):
            raise HTTPException(503, 'Private financial data needs review before it can be used.')
        state.setdefault(field, {})
    if not isinstance(state.get('monitoredAccounts', []), list):
        raise HTTPException(503, 'Private financial data needs review before it can be used.')
    state.setdefault('monitoredAccounts', [])
    if not isinstance(state.get('audit', []), list):
        raise HTTPException(503, 'Private financial data needs review before it can be used.')
    state.setdefault('audit', [])
    state['version'] = 2
    return state


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
            return empty_state(), None
        raise HTTPException(503, 'Private bank-sync storage is temporarily unavailable.') from None
    raw = r['Body'].read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise HTTPException(503, 'Private bank-sync storage needs review.')
    try:
        return migrate_state(json.loads(cipher().decrypt(raw))), r['ETag']
    except (InvalidToken, ValueError):
        raise HTTPException(503, 'Bank-sync storage could not be decrypted. Do not reconnect yet.') from None


def write_state(uid, state, etag):
    state = migrate_state(state)
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
    params = [('version', '2'), ('start-date', str(now - 90 * DAY)), ('end-date', str(now + 1))]
    if balances:
        params.append(('balances-only', '1'))
    else:
        params.extend(('account', x['id']) for x in selected)
        params.append(('pending', '1'))
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
                       'availableBalance': clean(a.get('available-balance'), 40) if a.get('available-balance') is not None else None,
                       'balanceDate': a.get('balance-date') if isinstance(a.get('balance-date'), (int, float)) else None})
    if len({a['key'] for a in result}) != len(result):
        raise HTTPException(502, 'SimpleFIN returned conflicting account identities.')
    return result


def money(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or abs(n) > 10**12:
            raise ValueError()
        return str(n)
    except (InvalidOperation, ValueError):
        raise HTTPException(502, 'SimpleFIN returned an invalid transaction amount.') from None


def record_balances(state, accounts, observed_at):
    history = state.setdefault('balanceHistory', {})
    for account in accounts:
        try:
            balance = money(account.get('balance'))
        except HTTPException:
            continue
        row = {'at': account.get('balanceDate') or observed_at, 'observedAt': observed_at,
               'balance': balance, 'currency': account.get('currency') or ''}
        available = account.get('availableBalance')
        if available not in (None, ''):
            try:
                row['availableBalance'] = money(available)
            except HTTPException:
                pass
        rows = history.setdefault(account['key'], [])
        if rows and all(rows[-1].get(k) == row.get(k) for k in ('at', 'balance', 'availableBalance', 'currency')):
            continue
        rows.append(row)
        history[account['key']] = rows[-400:]


def public_ref(account, transaction):
    return hashlib.sha256((account + '\0' + transaction).encode()).hexdigest()



def same_bonus_cycle(before, after):
    """Compare authoritative bonus-cycle identity, ignoring optional product metadata."""
    fields = ('entryId', 'bank', 'opened')
    return (isinstance(before, dict) and isinstance(after, dict)
            and all(isinstance(before.get(key), str) and before[key]
                    and before[key] == after.get(key) for key in fields))


def evidence_fingerprint(row):
    """Revision-safe identity: changed posted activity must return to the inbox."""
    packed = json.dumps([row.get(k) for k in (
        'posted', 'amount', 'description', 'pending', 'kind')],
        separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(packed.encode()).hexdigest()


def public_evidence_reviews(s):
    """Return only unchanged, currently linked review receipts using opaque identifiers."""
    result = {}
    for account, records in s.get('evidenceReviews', {}).items():
        mapping = s.get('mappings', {}).get(account)
        if not isinstance(mapping, dict) or not isinstance(records, dict):
            continue
        for raw_id, review in records.items():
            source = s.get('transactions', {}).get(account, {}).get(raw_id)
            if (not source or not isinstance(review, dict)
                    or not same_bonus_cycle(review.get('cycle'), mapping)
                    or review.get('fingerprint') != evidence_fingerprint(source)
                    or review.get('status') not in ('reviewed', 'dismissed')):
                continue
            result.setdefault(account, {})[public_ref(account, raw_id)] = {
                'status': review['status'],
                'reviewedAt': review['reviewedAt'],
                'requirementId': review.get('requirementId') or None}
    return result


def merchant_name(description):
    text = re.sub(r'\b(?:pos|debit|credit|purchase|payment|ach|card|online|pending)\b', ' ', description, flags=re.I)
    text = re.sub(r'[#*]?\d{4,}', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip(' -_/')
    return clean(text.title(), 80) or 'Transaction'


def automatic_category(row):
    # Read-only annotation: precedence includes zero/prenote verification,
    # genuine loan and mortgage payment categories and merchant suggestions.
    return category_for(row.get('description'), row.get('amount'),
                        pending=row.get('pending', False), kind=row.get('kind'))


def enrich_transactions(state, result):
    reviews = state.get('transactionReviews', {})
    records = []
    for account, rows in state.get('transactions', {}).items():
        public_rows = {}
        for raw_id, source in rows.items():
            if source.get('pendingClearedAt'):
                continue
            row = copy.deepcopy(source)
            ref = public_ref(account, raw_id)
            row['id'] = ref
            category, confidence, signals = automatic_category(row)
            review = reviews.get(account, {}).get(raw_id)
            if isinstance(review, dict) and review.get('category') in TRANSACTION_CATEGORIES:
                category, confidence = review['category'], 'confirmed'
                row['categorySource'] = 'user'
            else:
                row['categorySource'] = 'automatic'
                learned = state.get('merchantRules', {}).get(cache_key(row.get('description', '')))
                if isinstance(learned, dict) and learned.get('category') in TRANSACTION_CATEGORIES:
                    category, confidence = learned['category'], 'confirmed'
                    row['categorySource'] = 'user_rule'
                if category == 'unreviewed':
                    suggestion = state.get('financeAICache', {}).get(cache_key(row.get('description', '')))
                    if (isinstance(suggestion, dict) and
                            suggestion.get('category') in TRANSACTION_CATEGORIES and
                            suggestion.get('category') not in ('unreviewed', 'bonus')):
                        category, confidence = suggestion['category'], 'medium'
                        row['categorySource'] = 'ai'
                        row['confidenceScore'] = suggestion.get('confidenceScore')
            row.update(category=category, confidence=confidence, signals=signals,
                       merchant=merchant_name(row.get('description', '')),
                       stalePending=bool(row.get('pending') and row.get('lastObservedAt', 0) < int(time.time()) - 7 * DAY))
            public_rows[ref] = row
            records.append((account, raw_id, row))
        result.setdefault('transactions', {})[account] = public_rows

    # Only an *unambiguous, reciprocal* posted pair earns high-confidence transfer status.
    # Generic "payment" wording can describe payroll, purchases and refunds, so it must
    # not establish a transfer by itself. User-confirmed categories are never overridden.
    transfer_wording = re.compile(
        r'\b(?:transfer|xfer)\b|\b(?:credit card|card) payment\b|'
        r'\bpayment (?:thank you|to|from)\b', re.I)
    eligible = []
    for account, raw_id, row in records:
        if (row.get('pending') or row.get('categorySource') in ('user', 'user_rule')
                or Decimal(row['amount']) == 0
                or row.get('kind') in ('possible_bonus', 'possible_reversal')
                or not transfer_wording.search(row.get('description', ''))):
            continue
        eligible.append((account, raw_id, row))
    counterpart = {i: [] for i in range(len(eligible))}
    # Bucket by absolute value to avoid comparing unrelated amounts.
    by_amount = {}
    for i, (_, _, row) in enumerate(eligible):
        amount = Decimal(row['amount'])
        by_amount.setdefault(abs(amount), {'in': [], 'out': []})[
            'in' if amount > 0 else 'out'].append(i)
    for bucket in by_amount.values():
        for i in bucket['in']:
            account_i, _, in_row = eligible[i]
            for j in bucket['out']:
                account_j, _, out_row = eligible[j]
                if (account_i == account_j
                        or abs(in_row['posted'] - out_row['posted']) > 3 * DAY):
                    continue
                counterpart[i].append(j)
                counterpart[j].append(i)
    for i, matches in counterpart.items():
        if len(matches) != 1:
            continue
        j = matches[0]
        # Both ends must have exactly one candidate; otherwise a shared
        # amount can create multiple false high-confidence transfer matches.
        if j < i or counterpart[j] != [i]:
            continue
        for index in (i, j):
            row = eligible[index][2]
            row.update(category='transfer', confidence='high')
            row['signals'] = sorted(set(row['signals'] + ['matched_internal_transfer']))

    # Detect classic two-small-deposit account verification patterns. Keep raw
    # rows and review receipts untouched, and never override a user's correction.
    micro = micro_deposit_refs([
        {'accountKey': account, 'id': raw_id, 'description': row.get('description'),
         'amount': row.get('amount'), 'posted': row.get('posted'), 'pending': row.get('pending')}
        for account, raw_id, row in records
    ])
    for account, raw_id, row in records:
        if (account, raw_id) in micro and row.get('categorySource') not in ('user', 'user_rule'):
            row.update(category='verification', confidence='high', signals=['verification'])

    groups = {}
    for _, _, row in records:
        if row.get('pending') or Decimal(row['amount']) >= 0:
            continue
        groups.setdefault((row['merchant'].lower(), row['amount']), []).append(row)
    for rows in groups.values():
        ordered = sorted(rows, key=lambda r: r['posted'])
        if any(20 * DAY <= b['posted'] - a['posted'] <= 40 * DAY for a, b in zip(ordered, ordered[1:])):
            for row in ordered:
                row['signals'] = sorted(set(row['signals'] + ['possible_recurring']))
                if row['category'] == 'unreviewed':
                    row.update(category='subscription', confidence='medium')


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
    old_by_key = {a['key']: a for a in state['accounts']}
    # Account IDs can collide across provider connections. A transaction sync may update only
    # accounts already discovered for this owner; discovery is the only operation that adds accounts.
    fresh = [a for a in normalize_accounts(data) if a['key'] in old_by_key]
    fresh_by_key = {a['key']: a for a in fresh}
    state['accounts'] = list(fresh) + [a for k, a in old_by_key.items() if k not in fresh_by_key]
    record_balances(state, fresh, now)
    watched = set(state['mappings']) | set(state.get('monitoredAccounts', []))
    state['missingAccounts'] = sorted(k for k in watched if k not in fresh_by_key)
    warnings = [clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)]
    warnings += [clean(e) for e in data.get('errors', []) if isinstance(e, str)]
    state['warnings'] = warnings[:50]
    for a in data['accounts']:
        k = account_key(a)
        if k not in watched:
            continue
        rows = copy.deepcopy(state['transactions'].get(k, {}))
        seen_ids, invalid_count = set(), 0
        for t in a.get('transactions', []):
            if not isinstance(t, dict):
                invalid_count += 1
                continue
            timestamp = t.get('posted')
            if t.get('pending') is True and timestamp == 0:
                timestamp = t.get('transacted_at')
            if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                    or not 0 < timestamp <= now + 300):
                invalid_count += 1
                continue
            if not isinstance(t.get('id'), str) or not t['id']:
                invalid_count += 1
                continue
            try:
                amount = money(t.get('amount'))
            except HTTPException:
                invalid_count += 1
                continue
            seen_ids.add(t['id'])
            desc = clean(t.get('description'), 500)
            row = {'id': t['id'], 'posted': timestamp, 'amount': amount, 'description': desc,
                   'kind': classify(amount, desc), 'pending': t.get('pending') is True,
                   'observedAt': now, 'lastObservedAt': now}
            prior = rows.get(t['id'])
            if prior:
                row['observedAt'] = prior['observedAt']
                if any(prior.get(f) != row[f] for f in ('posted', 'amount', 'description', 'pending')):
                    row['changedAt'] = now
                    revisions = list(prior.get('revisions', []))
                    revisions.append({f: prior.get(f) for f in ('posted', 'amount', 'description', 'pending')})
                    row['revisions'] = revisions[-5:]
                    state.get('transactionReviews', {}).get(k, {}).pop(t['id'], None)
                    state.get('evidenceReviews', {}).get(k, {}).pop(t['id'], None)
                elif prior.get('changedAt'):
                    row['changedAt'] = prior['changedAt']
                    if prior.get('revisions'):
                        row['revisions'] = prior['revisions']
            rows[t['id']] = row
        if invalid_count:
            warnings.append('One account contained '+str(invalid_count)+' invalid transaction row(s); skipped for safety.')
            state['warnings'] = warnings[:50]
        # Preserve pending originals in encrypted storage, but hide a cleared
        # authorization once its date is covered by a later provider response.
        for row_id, row in rows.items():
            if (row.get('pending') and not row.get('pendingClearedAt')
                    and row_id not in seen_ids
                    and now - 90 * DAY <= row.get('posted', 0) <= now):
                row['pendingClearedAt'] = now
        # Missing posted rows never imply reversal/deletion; preserve 90 days of observed history.
        state['transactions'][k] = {i: r for i, r in rows.items() if r['posted'] >= now - 90 * DAY}
        state.setdefault('evidenceReviews', {})[k] = {
            i: v for i, v in state.get('evidenceReviews', {}).get(k, {}).items()
            if i in state['transactions'][k] and isinstance(v, dict)
            and v.get('fingerprint') == evidence_fingerprint(state['transactions'][k][i])}
    state['lastSuccess'] = now
    state['windowStart'] = now - 30 * DAY
    state['windowEnd'] = now
    state['status'] = 'attention' if warnings or state['missingAccounts'] else 'connected'
    return state


def public_state(s):
    # Never serialize the provider credential, setup token, or storage key.
    fields = ('accounts', 'mappings', 'monitoredAccounts', 'warnings', 'missingAccounts', 'lastSuccess',
              'lastAttempt', 'windowStart', 'windowEnd', 'status', 'nextSyncAt', 'error',
              'accountProfiles', 'balanceHistory')
    out = {k: copy.deepcopy(s[k]) for k in fields if k in s}
    # Provider IDs and connection IDs are needed server-side for syncing, not in the browser.
    out['accounts'] = [{k: v for k, v in a.items() if k not in ('id', 'connection')}
                       for a in s.get('accounts', [])]
    out['monitoredAccounts'] = list(s.get('monitoredAccounts', []))
    out['transactions'] = {}
    enrich_transactions(s, out)
    out['evidenceReviews'] = public_evidence_reviews(s)
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
    operation = s.pop('operation', None)
    if operation:
        # Bounded encrypted audit metadata; never include credentials, balances or transaction text.
        s['audit'] = (s.get('audit', []) + [{'operation': operation, 'at': int(time.time()),
                                             'status': s.get('status', 'unknown')}])[-100:]
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
    # Linking authorizes read-only activity for every returned account. No separate toggle.
    s['monitoredAccounts'] = sorted({a['key'] for a in s['accounts']})
    watched = set(s['mappings']) | set(s['monitoredAccounts'])
    selected = [a for a in s['accounts'] if a['key'] in watched]
    if not selected:
        return finish(uid, s, tag)
    s['lastAttempt'] = now
    # Reserve the next attempt before network I/O. Restart/timeout cannot exhaust provider quotas.
    s['nextSyncAt'] = now + DAY
    tag = write_state(uid, s, tag)
    try:
        candidate = merge(copy.deepcopy(s), get_data(s['access'], selected), now)
        # Optional, bounded Groq classification occurs only after successful
        # provider data retrieval; it never writes raw activity or milestones.
        try:
            classify_unknown(candidate, now=now)
        except Exception:
            pass  # AI must not block a verified bank sync.
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
    # User-selected structured promotion product; never guessed from provider names.
    productId: str | None = Field(default=None, pattern=r'^[a-z0-9_-]{1,40}$')


    @field_validator('opened')
    @classmethod
    def valid_opened(cls, v):
        from datetime import date
        date.fromisoformat(v)
        return v


class SelectionBody(BaseModel):
    mappings: dict[str, Mapping] = Field(max_length=50)


class MonitoringBody(BaseModel):
    accounts: list[str] = Field(max_length=50)


class AccountProfile(BaseModel):
    kind: str
    order: int = Field(ge=0, le=499)
    balanceMode: str = 'exclude'
    hidden: bool | None = None

    @field_validator('kind')
    @classmethod
    def valid_kind(cls, value):
        if value not in ACCOUNT_KINDS:
            raise ValueError('Unsupported account type')
        return value

    @field_validator('balanceMode')
    @classmethod
    def valid_balance_mode(cls, value):
        if value not in BALANCE_MODES:
            raise ValueError('Unsupported balance treatment')
        return value


class AccountProfilesBody(BaseModel):
    profiles: dict[str, AccountProfile] = Field(max_length=500)


class EvidenceReviewBody(BaseModel):
    account: str = Field(pattern=r'^[0-9a-f]{64}$')
    transaction: str = Field(pattern=r'^[0-9a-f]{64}$')
    entryId: str = Field(min_length=1, max_length=120)
    bank: str = Field(min_length=1, max_length=120)
    opened: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    decision: str
    requirementId: str | None = Field(default=None, max_length=40)

    @field_validator('decision')
    @classmethod
    def valid_decision(cls, value):
        if value not in ('reviewed', 'dismissed', 'clear'):
            raise ValueError('Unsupported evidence review decision')
        return value

    @field_validator('requirementId')
    @classmethod
    def valid_requirement_id(cls, value):
        if value is not None and not re.fullmatch(r'[a-z0-9_-]{1,40}', value):
            raise ValueError('Invalid requirement reference')
        return value


class TransactionReviewBody(BaseModel):
    account: str = Field(pattern=r'^[0-9a-f]{64}$')
    transaction: str = Field(pattern=r'^[0-9a-f]{64}$')
    category: str
    applySimilar: bool = False

    @field_validator('category')
    @classmethod
    def valid_category(cls, value):
        if value not in TRANSACTION_CATEGORIES:
            raise ValueError('Unsupported transaction category')
        return value


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
        s['monitoredAccounts'] = sorted(a['key'] for a in s['accounts'])
        observed = int(time.time())
        record_balances(s, s['accounts'], observed)
        # Reserve the daily provider budget before automatic first import.
        if s['accounts']:
            s['lastAttempt'], s['nextSyncAt'] = observed, observed + DAY
            tag = write_state(uid, s, tag)
            try:
                s = merge(copy.deepcopy(s), get_data(access, s['accounts']), observed)
                s.pop('error', None)
            except HTTPException as activity_error:
                s['error'] = activity_error.detail
                s['status'] = 'attention'
        s['warnings'] = ([clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)] +
                         [clean(e) for e in data.get('errors', []) if isinstance(e, str)])[:50]
        if s['warnings']:
            s['status'] = 'attention'
    except (HTTPException, UnicodeError) as e:
        s['error'] = e.detail if isinstance(e, HTTPException) else 'SimpleFIN returned an invalid response.'
        s['status'] = 'attention'
    return finish(uid, s, tag)


@router.post('/reconnect')
def reconnect(body: ConnectBody, request: Request):
    """Replace rejected provider access while retaining owner history, profiles and mappings."""
    uid = owner(request)
    cipher()
    url = claim_url(body.setup_token)
    s, tag = reserve(uid, 'reconnect')
    if not s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'Connect SimpleFIN first.')
    try:
        access = network(url, method='POST').decode().strip()
        safe_url(access)
        s['access'] = access
        s['status'] = 'connected'
        s['nextSyncAt'] = 0
        s['nextDiscoveryAt'] = 0
        s.pop('error', None)
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
        watched = set(s['mappings']) | set(s.get('monitoredAccounts', []))
        s['missingAccounts'] = sorted(k for k in watched if k not in {a['key'] for a in fresh})
        current = {a['key']: a for a in fresh}
        # Preserve missing matched accounts so an omitted response cannot silently delete a match.
        for a in s['accounts']:
            if a['key'] in watched and a['key'] not in current:
                fresh.append(a)
        s['accounts'] = fresh
        s['monitoredAccounts'] = sorted({a['key'] for a in fresh})
        s.pop('error', None)
        record_balances(s, [a for a in fresh if a['key'] in current], now)
        if fresh and now >= s.get('nextSyncAt', 0):
            s['lastAttempt'], s['nextSyncAt'] = now, now + DAY
            tag = write_state(uid, s, tag)
            try:
                s = merge(copy.deepcopy(s), get_data(s['access'], fresh), now)
                s.pop('error', None)
            except HTTPException as activity_error:
                s['error'] = activity_error.detail
                s['status'] = 'attention'
        s['warnings'] = ([clean(e.get('msg')) for e in data.get('errlist', []) if isinstance(e, dict)] +
                         [clean(e) for e in data.get('errors', []) if isinstance(e, str)])[:50]
        s['status'] = 'attention' if s.get('error') or s['warnings'] or s.get('missingAccounts') else 'connected'
    except HTTPException as e:
        s['error'] = e.detail
        s['status'] = 'attention'
    return finish(uid, s, tag)


def apply_mapping_selection(s, mappings):
    """Shared provider-independent, exact-cycle mapping transition."""
    # Product assignments are optional metadata; correcting them must not erase
    # already observed posted transactions or consume an extra provider sync.
    previous_mappings = s['mappings']
    s['transactions'] = {k: v for k, v in s['transactions'].items()
                         if k in s.get('monitoredAccounts', []) or (
                             k in mappings and same_bonus_cycle(previous_mappings.get(k), mappings[k]))}
    # A product-only correction is not a new promotion cycle: keep review
    # decisions and transaction evidence. A requirement association, however,
    # is product-specific and must be re-verified after a product change.
    retained = {}
    for key, reviews in s.get('evidenceReviews', {}).items():
        if (key not in mappings or not same_bonus_cycle(previous_mappings.get(key), mappings[key])
                or not isinstance(reviews, dict)):
            continue
        product_changed = (previous_mappings[key].get('productId') != mappings[key].get('productId'))
        retained[key] = {
            raw: ({**review, 'requirementId': None} if product_changed else review)
            for raw, review in reviews.items() if isinstance(review, dict)}
    s['mappings'] = mappings
    s['evidenceReviews'] = retained


@router.put('/selection')
def select(body: SelectionBody, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'selection')
    allowed = {a['key'] for a in s['accounts']}
    if not s.get('access') or not set(body.mappings).issubset(allowed):
        finish(uid, s, tag)
        raise HTTPException(400, 'Choose accounts from the current connection.')
    mappings = {k: v.model_dump(exclude_none=True) for k, v in body.mappings.items()}
    apply_mapping_selection(s, mappings)
    return finish(uid, s, tag)


@router.put('/monitoring')
def monitor(body: MonitoringBody, request: Request):
    """Explicit opt-in for independent, read-only transaction monitoring."""
    uid = owner(request)
    s, tag = reserve(uid, 'monitoring')
    allowed = {a['key'] for a in s['accounts']}
    chosen = set(body.accounts)
    if (not s.get('access') or len(chosen) != len(body.accounts)
            or not chosen.issubset(allowed)
            or any(not re.fullmatch(r'[0-9a-f]{64}', k) for k in chosen)):
        finish(uid, s, tag)
        raise HTTPException(400, 'Choose unique accounts from the current connection.')
    s['monitoredAccounts'] = sorted(chosen)
    s['transactions'] = {k: v for k, v in s['transactions'].items()
                         if k in chosen or k in s['mappings']}
    s['evidenceReviews'] = {k: v for k, v in s.get('evidenceReviews', {}).items()
                            if k in s['transactions']}
    # First opt-in can begin immediately. Existing provider attempts remain rate-limited.
    if chosen and not s.get('lastAttempt') and not s.get('nextSyncAt'):
        s['nextSyncAt'] = 0
    return finish(uid, s, tag)


@router.put('/account-profiles')
def account_profiles(body: AccountProfilesBody, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'account-profiles')
    allowed = {a['key'] for a in s['accounts']}
    if not set(body.profiles).issubset(allowed) or any(not re.fullmatch(r'[0-9a-f]{64}', k) for k in body.profiles):
        finish(uid, s, tag)
        raise HTTPException(400, 'Choose accounts from the current connection.')
    supplied = {k: v.model_dump(exclude_none=True) for k, v in body.profiles.items()}
    # Legacy clients omit visibility. Never accidentally unhide a saved account
    # when an older Finance version saves unrelated profile settings.
    for k, profile in supplied.items():
        if 'hidden' not in profile and s.get('accountProfiles', {}).get(k, {}).get('hidden') is True:
            profile['hidden'] = True
    # Keep profiles for temporarily missing accounts; replace current connected-account profiles atomically.
    s['accountProfiles'] = {k: v for k, v in s.get('accountProfiles', {}).items() if k not in allowed}
    s['accountProfiles'].update(supplied)
    return finish(uid, s, tag)


@router.put('/transaction-review')
def transaction_review(body: TransactionReviewBody, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'transaction-review')
    rows = s.get('transactions', {}).get(body.account, {})
    matches = [raw for raw in rows if public_ref(body.account, raw) == body.transaction]
    if len(matches) != 1:
        finish(uid, s, tag)
        raise HTTPException(400, 'Choose a saved transaction from this account.')
    raw = matches[0]
    if body.category == 'unreviewed':
        s.setdefault('transactionReviews', {}).setdefault(body.account, {}).pop(raw, None)
    else:
        s.setdefault('transactionReviews', {}).setdefault(body.account, {})[raw] = {
            'category': body.category, 'reviewedAt': int(time.time())}
        if body.applySimilar:
            name = s['transactions'][body.account][raw].get('description', '')
            s.setdefault('merchantRules', {})[cache_key(name)] = {
                'category': body.category, 'updatedAt': int(time.time())}
    return finish(uid, s, tag)


def apply_evidence_decision(s, body, public=None):
    """Shared revision-bound review receipt; never changes Tracker milestones."""
    mapping = s.get('mappings', {}).get(body.account)
    cycle = {'entryId': body.entryId, 'bank': body.bank, 'opened': body.opened}
    rows = s.get('transactions', {}).get(body.account, {})
    matches = [raw for raw in rows if public_ref(body.account, raw) == body.transaction]
    if (not s.get('access') or not same_bonus_cycle(mapping, cycle) or len(matches) != 1):
        raise HTTPException(400, 'The account, transaction or bonus cycle no longer matches.')
    raw = matches[0]
    row = rows[raw]
    from datetime import date
    try:
        opened = date.fromisoformat(body.opened)
    except ValueError:
        raise HTTPException(400, 'The bonus opening date is invalid.')
    if (row.get('pending') or row.get('posted', 0) < (
            opened - date(1970, 1, 1)).days * DAY
            or row.get('posted', 0) > time.time() + 300):
        raise HTTPException(400, 'This transaction is not posted in the saved bonus cycle.')
    public = public_state(s) if public is None else public
    candidate = public.get('transactions', {}).get(body.account, {}).get(body.transaction)
    signals = candidate.get('signals', []) if candidate else []
    recognized = ('possible_bonus', 'possible_direct_deposit', 'possible_fee',
                  'possible_reversal', 'possible_recurring')
    amount = Decimal(row['amount'])
    is_deposit = (amount > 0 and 'possible_direct_deposit' in signals
                  and candidate.get('kind') != 'possible_bonus')
    if body.requirementId is not None and (body.decision != 'reviewed' or not is_deposit):
        raise HTTPException(400, 'A requirement may be associated only with a reviewed deposit candidate.')
    if (not any(signal in signals for signal in recognized)
            and candidate.get('kind') not in ('possible_bonus', 'possible_reversal')):
        raise HTTPException(400, 'This transaction has no supported review evidence.')
    reviews = s.setdefault('evidenceReviews', {}).setdefault(body.account, {})
    if body.decision == 'clear':
        reviews.pop(raw, None)
    else:
        reviews[raw] = {
            'cycle': copy.deepcopy(cycle),
            'fingerprint': evidence_fingerprint(row),
            'status': body.decision, 'reviewedAt': int(time.time()),
            'requirementId': body.requirementId if body.decision == 'reviewed' else None}


@router.put('/evidence-review')
def evidence_review(body: EvidenceReviewBody, request: Request):
    """Private, revision-bound receipts; never changes a promotion milestone."""
    uid = owner(request)
    s, tag = reserve(uid, 'evidence-review')
    try:
        apply_evidence_decision(s, body)
    except HTTPException:
        finish(uid, s, tag)
        raise
    return finish(uid, s, tag)


@router.post('/sync')
def sync_endpoint(request: Request):
    return sync(owner(request))


@router.delete('')
def disconnect(request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'disconnect')
    # Tombstone instead of delete so concurrent stale writes cannot recreate credentials.
    tombstone = empty_state()
    tombstone['status'] = 'disconnected'
    return finish(uid, tombstone, tag)


async def scheduled_simplefin_loop():
    while True:
        try:
            if os.getenv('SIMPLEFIN_ENCRYPTION_KEY') and os.getenv('AUTHORIZED_USER_ID'):
                uid = hashlib.sha256(os.environ['AUTHORIZED_USER_ID'].strip().encode()).hexdigest()
                s, _ = await run_in_threadpool(read_state, uid)
                if s.get('access') and s.get('accounts') and time.time() >= s.get('nextSyncAt', 0):
                    await run_in_threadpool(sync, uid)
        except Exception as error:
            # Never log repr(error) or provider payload; both can contain secrets.
            BACKGROUND_LOGGER.warning("simplefin_scheduled_sync_error class=%s", type(error).__name__)
        await asyncio.sleep(61)

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
from finance_category_rules import category_for, micro_deposit_refs, annotate_recurring_rent
from finance_income_rules import annotate_paychecks
from finance_ai_categories import classify_unknown, cache_key
from finance_history_archive import archive_old, read_month, HOT_DAYS, purge_account
from finance_account_management import (AccountAction, institution_handle, syncing, public_accounts,
    merge_profiles, action as account_action, purge_history, filter_after_removal)

def private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'


from fastapi import Depends
router = APIRouter(prefix='/api/bonus-tracker/simplefin', dependencies=[Depends(private_response)])
BACKGROUND_LOGGER = logging.getLogger(__name__)

DAY = 86400
FULL_SYNC_INTERVAL = 2 * 3600
BALANCE_SYNC_INTERVAL = 2 * 3600
AUTOMATIC_REQUEST_RESERVE = 4
MAX_STATE = 8 * 1024 * 1024
HOSTS = {'bridge.simplefin.org', 'beta-bridge.simplefin.org'}
ACCOUNT_KINDS = frozenset(('unclassified', 'checking', 'savings', 'credit_card', 'brokerage',
                           'retirement', 'loan', 'mortgage', 'other_asset', 'other_liability'))
BALANCE_MODES = frozenset(('exclude', 'asset', 'liability'))
TRANSACTION_CATEGORIES = frozenset(('unreviewed', 'income', 'housing', 'utilities', 'groceries',
                                    'dining', 'transportation', 'shopping', 'insurance', 'health', 'entertainment',
                                    'fees', 'taxes', 'travel', 'subscription', 'transfer', 'loan_proceeds', 'refund',
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
    state.setdefault('historyMonths', [])
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
    # Preserve old activity in verified encrypted monthly shards before pruning.
    hot, archived = archive_old(store(), cipher(), 'simplefin', uid,
                                state.get('transactions', {}), int(time.time()))
    state['transactions'] = hot
    state['historyMonths'] = sorted(set(state.get('historyMonths', [])) | set(archived))
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
    params = [('version', '2'), ('start-date', str(now - 89 * DAY)), ('end-date', str(now))]
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


# SimpleFIN warning payloads are not all errors. Exceeding a provider's
# historical window and partial-history notices must never mark a bank broken.
INFORMATION_NOTICE = re.compile(
    r'(?:date\s*range.*(?:limit|capp)|requested\s*date\s*range|'
    r'partial\s*(?:data|history|transaction)|history\s*(?:truncat|limit)|'
    r'older\s*transactions\s*(?:unavailable|not\s*available)|'
    r'90[-\s]?day\s*(?:limit|window))',re.I)
REJECTED_NOTICE = re.compile(r'(?:access\s*(?:denied|rejected)|authorization\s*(?:denied|rejected)|'
    r'not\s*authorized|forbidden|invalid\s*access|credentials?\s*(?:rejected|invalid)|'
    r'permission\s*denied)',re.I)
BANK_LOGIN_NOTICE = re.compile(r'(?:sign\s*in|log\s*in|login|reauthenticat|'
    r'credentials?\s*(?:expired|required)|bank\s*requires\s*action|'
    r'update\s*(?:your\s*)?login|mfa\s*required)',re.I)
CONNECTION_NOTICE = re.compile(r'(?:connection\s*(?:error|failed|broken)|'
    r'unable\s*to\s*connect|could\s*not\s*connect|bank\s*(?:unavailable|offline)|'
    r'provider\s*connection\s*failed)',re.I)


def warning_details(data):
    """Separate provider notices from actionable, bank-scoped access failures."""
    conns = {str(c.get('conn_id')): clean(c.get('name'),100)
             for c in data.get('connections', []) if isinstance(c, dict)}
    notices, warnings, issues = [], [], {}
    records = [(e, e.get('msg') if isinstance(e, dict) else e)
               for group in ('errlist', 'errors') for e in data.get(group, [])
               if isinstance(e, (dict, str))]
    for raw, value in records:
        msg = clean(value)
        if not msg:
            continue
        if INFORMATION_NOTICE.search(msg):
            notices.append(msg)
            continue
        if REJECTED_NOTICE.search(msg):
            kind = 'rejected'
        elif BANK_LOGIN_NOTICE.search(msg):
            kind = 'bank_login'
        elif CONNECTION_NOTICE.search(msg):
            kind = 'connection'
        else:
            notices.append(msg)
            continue
        warnings.append(msg)
        explicit = str((raw.get('conn_id') or raw.get('connection_id') or raw.get('connection')) if isinstance(raw, dict) else '')
        matches = [explicit] if explicit and explicit in conns else [
            cid for cid, name in conns.items() if name and name.casefold() in msg.casefold()]
        # Unattributed issues must not smear red indicators across every bank.
        if len(matches) == 1:
            issues[matches[0]] = {'kind': kind, 'reason': msg}
    return notices[:50], warnings[:50], issues


def update_provider_notices(state, data):
    notices, warnings, issues = warning_details(data)
    state['notices'] = notices
    state['warnings'] = warnings
    state['institutionIssues'] = issues
    return warnings, issues


def set_provider_status(state):
    state['status'] = 'attention' if state.get('error') or state.get('warnings') or state.get('missingAccounts') else 'connected'
    return state['status']


def reclassify_stored_warnings(state):
    """Migrate legacy informational warnings without altering account or ledger data.

    Earlier releases persisted SimpleFIN's capped-range/partial-history notices
    as actionable warnings. Reclassify the stored strings on a status read so an
    ordinary GET fixes the banner without a new provider sync.
    """
    if not state.get('access'):
        return False
    old_warnings = state.get('warnings') or []
    if not isinstance(old_warnings, list):
        return False  # Never silently discard malformed stored diagnostics.
    old_notices = state.get('notices') or []
    if not isinstance(old_notices, list):
        old_notices = []
    real_warnings, moved_notices = [], []
    for warning in old_warnings:
        # The same classifier used for fresh provider responses identifies
        # informational messages. Preserve all other warnings, including
        # invalid-transaction warnings, rather than downgrading unknown errors.
        message = clean(warning)
        if INFORMATION_NOTICE.search(message):
            moved_notices.append(message)
        else:
            real_warnings.append(warning)
    new_notices = list(dict.fromkeys([*old_notices, *moved_notices]))[:50]
    old_issues = state.get('institutionIssues') or {}
    new_issues = (dict(old_issues) if isinstance(old_issues, dict) else {})
    for key, issue in list(new_issues.items()):
        if isinstance(issue, dict) and INFORMATION_NOTICE.search(clean(issue.get('reason'))):
            del new_issues[key]
    changed = (real_warnings != old_warnings or new_notices != old_notices
               or new_issues != old_issues)
    if changed:
        state['warnings'] = real_warnings
        state['notices'] = new_notices
        state['institutionIssues'] = new_issues
    if state.get('status') in ('attention', 'connected'):
        previous = state.get('status')
        set_provider_status(state)
        changed = changed or state['status'] != previous
    return changed



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
                       'mask': (str(a.get('mask') or a.get('last4') or '')[-4:] if str(a.get('mask') or a.get('last4') or '').isdigit() and len(str(a.get('mask') or a.get('last4') or '')) == 4 else None),
                       'currency': clean(a.get('currency'), 40), 'balance': clean(a.get('balance'), 40),
                       'availableBalance': clean(a.get('available-balance'), 40) if a.get('available-balance') is not None else None,
                       'balanceDate': a.get('balance-date') if isinstance(a.get('balance-date'), (int, float)) else None,
                       'accountType': clean(a.get('type') or a.get('account-type'), 40),
                       'accountSubtype': clean(a.get('subtype') or a.get('account-subtype'), 40)})
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
        # At most one daily point for 90 days, then weekly through 24 months.
        from datetime import datetime, timezone
        seen = set()
        compact = []
        for point in reversed(rows):
            at = point.get('observedAt', 0)
            if not isinstance(at, (int, float)) or at < observed_at - 731 * DAY:
                continue
            day = datetime.fromtimestamp(at, timezone.utc).date()
            bucket = str(day) if at >= observed_at - 90 * DAY else str(day.isocalendar()[:2])
            if bucket not in seen:
                seen.add(bucket)
                compact.append(point)
        history[account['key']] = list(reversed(compact))


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
    title = clean(text.title(), 80)
    # Avoid generic or blank merchant headings when the bank provided more
    # useful original wording; do not lose it to keyword cleanup.
    if not title or title.lower() in ('transaction', 'transfer', 'payment', 'bank transaction'):
        return clean(description, 80) or 'Transaction'
    return title


def merchant_rule_key(account, description):
    """Scope new learned category rules to their original linked account."""
    return str(account) + ':' + cache_key(description)


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
            plaidCategory = row.get('plaidSuggestedCategory')
            if (plaidCategory in TRANSACTION_CATEGORIES and row.get('kind') not in ('possible_bonus','possible_reversal')
                    and category not in ('transfer','verification','bonus','refund','loan_proceeds') and not row.get('pending')
                    and not ('possible_transfer' in signals)):
                category, confidence = plaidCategory, ('high' if row.get('plaidConfidence') in ('VERY_HIGH','HIGH') else 'medium')
                signals = sorted(set(signals + (row.get('plaidCategorySignals') or [])))
                row['categorySource'] = 'plaid'
            review = reviews.get(account, {}).get(raw_id)
            if isinstance(review, dict) and review.get('category') in TRANSACTION_CATEGORIES:
                category, confidence = review['category'], 'confirmed'
                row['categorySource'] = 'user'
            else:
                row.setdefault('categorySource','automatic')
                description = row.get('description', '')
                rules = state.get('merchantRules', {})
                learned = rules.get(merchant_rule_key(account, description))
                # Legacy global rules remain valid for distinctive descriptions,
                # but generic labels must not classify an unrelated bank account.
                if learned is None and not re.fullmatch(r'\s*(?:payment(?: received)?|transfer|transaction|deposit)\s*', str(description), re.I):
                    learned = rules.get(cache_key(description))
                if isinstance(learned, dict) and learned.get('category') in TRANSACTION_CATEGORIES:
                    category, confidence = learned['category'], 'confirmed'
                    row['categorySource'] = 'user_rule'
                if category == 'unreviewed':
                    suggestion = state.get('financeAICache', {}).get(cache_key(row.get('description', '')))
                    if (isinstance(suggestion, dict) and
                            suggestion.get('category') in TRANSACTION_CATEGORIES and
                            suggestion.get('category') not in ('unreviewed', 'bonus')):
                        if suggestion['category'] != 'subscription' or float(suggestion.get('confidenceScore') or 0) >= .90:
                            category, confidence = suggestion['category'], 'medium'
                            row['categorySource'] = 'ai'
                            row['confidenceScore'] = suggestion.get('confidenceScore')
            row.update(category=category, confidence=confidence, signals=signals,
                       merchant=clean(row.get('merchantName'),100) or merchant_name(row.get('description', '')),
                       stalePending=bool(row.get('pending') and row.get('lastObservedAt', 0) < int(time.time()) - 7 * DAY))
            public_rows[ref] = row
            records.append((account, raw_id, row))
        result.setdefault('transactions', {})[account] = public_rows

    # Only unique reciprocal posted pairs within three days are matched, regardless
    # of description or exact cents. Accounts at the same institution may also match.
    # User-confirmed categories are never overridden.
    eligible = []
    for account, raw_id, row in records:
        if (row.get('pending') or row.get('categorySource') in ('user', 'user_rule')
                or Decimal(row['amount']) == 0
                or row.get('kind') in ('possible_bonus', 'possible_reversal')
                or (row.get('category') == 'income' and 'paycheck' in row.get('signals', []))
                or row.get('category') in ('bonus','refund','verification','loan_proceeds','groceries',
                    'dining','shopping','housing','insurance','utilities','transportation',
                    'health','entertainment','fees','taxes','travel','subscription','loan')
                or not isinstance(row.get('posted'), (int, float))):
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

    # Employer recurrence is inferred from presentation rows only. Money movements
    # are paired first and are never counted as paychecks.
    for account, _raw, row in records:
        row['accountKey'] = account
    annotate_paychecks([row for _account, _raw, row in records])
    annotate_recurring_rent([row for _account, _raw, row in records])

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
                # A recurring debit is evidence, not proof of a subscription.


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
    if re.search(r'\bbonus\b|promotional (?:credit|payment|reward)|promotion(?:al)?\s+reward|sign[- ]?up\s+reward', text):
        return 'possible_bonus'
    return 'deposit'


def merge(state, data, now):
    old_by_key = {a['key']: a for a in state['accounts']}
    # Account IDs can collide across provider connections. A transaction sync may update only
    # accounts already discovered for this owner; discovery is the only operation that adds accounts.
    fresh = [a for a in normalize_accounts(data) if a['key'] in old_by_key and syncing(state, a)]
    for a in fresh:
        a['firstSeenAt'] = old_by_key[a['key']].get('firstSeenAt', now)
    fresh_by_key = {a['key']: a for a in fresh}
    state['accounts'] = list(fresh) + [a for k, a in old_by_key.items() if k not in fresh_by_key]
    record_balances(state, fresh, now)
    watched = {a['key'] for a in state['accounts'] if syncing(state, a)} & (set(state['mappings']) | set(state.get('monitoredAccounts', [])))
    state['missingAccounts'] = sorted(k for k in watched if k not in fresh_by_key)
    warnings, issues = update_provider_notices(state, data)
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
            if timestamp <= state.get('accountExclusions', {}).get(k, {}).get('deletedBefore', 0):
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
        state.setdefault('invalidTransactionsByAccount', {})[k] = invalid_count
        if invalid_count:
            warnings.append('One account contained '+str(invalid_count)+' invalid transaction row(s); skipped for safety.')
            state['warnings'] = warnings[:50]
        # Preserve pending originals in encrypted storage, but hide a cleared
        # authorization once its date is covered by a later provider response.
        for row_id, row in rows.items():
            if (row.get('pending') and not row.get('pendingClearedAt')
                    and not invalid_count and not warnings and row_id not in seen_ids
                    and now - 90 * DAY <= row.get('posted', 0) <= now):
                row['pendingClearedAt'] = now
        # Missing posted rows never imply reversal/deletion; preserve 90 days of observed history.
        state['transactions'][k] = rows  # Archived only after verified conditional R2 write.
        state.setdefault('evidenceReviews', {})[k] = {
            i: v for i, v in state.get('evidenceReviews', {}).get(k, {}).items()
            if i in state['transactions'][k] and isinstance(v, dict)
            and v.get('fingerprint') == evidence_fingerprint(state['transactions'][k][i])}
    state['syncFailureAttempts'] = 0
    state['health'] = {'lastSuccess': now, 'lastError': None, 'nextRetry': None}
    state['lastSuccess'] = now
    state['windowStart'] = now - 30 * DAY
    state['windowEnd'] = now
    set_provider_status(state)
    return state


def public_state(s):
    # Never serialize the provider credential, setup token, or storage key.
    fields = ('accounts', 'mappings', 'monitoredAccounts', 'lastAccountDiscoveryAt', 'lastNewAccountsAt', 'newAccountsLastSeenCount', 'warnings', 'notices', 'institutionIssues', 'missingAccounts', 'lastSuccess',
              'lastAttempt', 'windowStart', 'windowEnd', 'status', 'nextSyncAt', 'nextGuidedDiscoveryAt',
              'lastBalanceRefreshAt', 'nextBalanceRefreshAt', 'balanceRefreshErrorKind', 'newBankDiscoveryNextAt', 'error',
              'accountProfiles', 'balanceHistory', 'health', 'accountExclusions', 'financeAILastSync')
    out = {k: copy.deepcopy(s[k]) for k in fields if k in s}
    # Provider IDs and connection IDs are needed server-side for syncing, not in the browser.
    out['accounts'] = [{k: v for k, v in a.items() if k not in ('id', 'connection')}
                       for a in public_accounts(s, s.get('accounts', []))]
    for a in out['accounts']:
        conn = next((x.get('connection') for x in s.get('accounts', []) if x['key'] == a['key']), None)
        issue = s.get('institutionIssues', {}).get(conn, {})
        if issue:
            a['attentionReason'] = issue['reason']
            a['attentionKind'] = issue['kind']
        elif a['key'] in s.get('missingAccounts', []):
            a['attentionReason'] = 'Account missing from the latest SimpleFIN response.'
            a['attentionKind'] = 'connection'
        a['invalidTransactionRows'] = s.get('invalidTransactionsByAccount', {}).get(a['key'], a.get('invalidTransactionRows', 0))
    out['monitoredAccounts'] = sorted(set(s.get('monitoredAccounts', [])) | (set(s.get('transactions', {})) & {a['key'] for a in s.get('accounts', [])}))
    out['historyMonths'] = list(s.get('historyMonths', []))
    out['transactions'] = {}
    enrich_transactions(s, out)
    filter_after_removal(s, out['transactions'])
    out['evidenceReviews'] = public_evidence_reviews(s)
    out.update(connected=bool(s.get('access')), configured=True, evidenceOnly=True, financeVersion='3.4.98')
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


def claim_request(state, now):
    attempts = [at for at in state.get('requestAttempts', []) if at > now - DAY]
    if len(attempts) >= 24:
        state['nextSyncAt'] = min(attempts) + DAY
        raise HTTPException(429, 'Daily bank refresh limit reached. Updates resume automatically.')
    state['requestAttempts'] = attempts + [now]



def refresh_balances(uid, *, force=False):
    """Hourly alternating account discovery/refresh between full 2-hour pulls.

    No background request can bypass the same 24-call rolling account quota.
    History, bonus reviews, user corrections and the next transaction cursor
    are never changed by a balance-only response.
    """
    s, tag = reserve(uid, 'balance-refresh')
    now = int(time.time())
    if (not s.get('access') or
            (force and now < s.get('newBankDiscoveryNextAt', 0)) or
            (not force and now < s.get('nextBalanceRefreshAt', 0)) or
            (not force and s.get('accounts') and now >= s.get('nextSyncAt', 0))):
        return finish(uid, s, tag)
    remaining, _reset = _remaining_discovery_budget(s, now)
    if force and remaining <= AUTOMATIC_REQUEST_RESERVE:
        s['balanceRefreshErrorKind'] = 429
        return finish(uid, s, tag)
    s['nextBalanceRefreshAt'] = now + (BALANCE_SYNC_INTERVAL if any(syncing(s,a) for a in s.get('accounts', [])) else 3600)
    if force:
        # Failed startup probes cannot loop every scheduler minute or waste
        # the shared provider budget while waiting for delayed Bridge data.
        s['autoStartupRetryAt'] = now + 3600
    # Returning from the external Bridge may request an immediate check,
    # but never more than once per 90 seconds or outside the shared budget.
    s['newBankDiscoveryNextAt'] = now + 90
    try:
        claim_request(s, now)
        tag = write_state(uid, s, tag)
        data = get_data(s['access'], balances=True)
        fresh = {row['key']: row for row in normalize_accounts(data)}
        if not fresh and s.get('accounts'):
            raise HTTPException(502, 'SimpleFIN returned no accounts. Saved accounts are unchanged.')
        prior = {row['key']: row for row in s.get('accounts', [])}
        # Preserve existing identity, profiles, monitoring preferences, reviews
        # and transaction history; never replace an existing row from an
        # accounts-only response or lose one because SimpleFIN omitted it.
        discovered = []
        for key, row in fresh.items():
            if key not in prior:
                row['firstSeenAt'] = now
                row['discovered'] = True
                prior[key] = row
                discovered.append(row)
            else:
                existing = prior[key]
                for field in ('name','bank','currency','balance','availableBalance',
                              'balanceDate','accountType','accountSubtype','mask'):
                    if row.get(field) is not None:
                        existing[field] = row[field]
        s['accounts'] = list(prior.values())
        record_balances(s, [prior[key] for key in fresh], now)
        s['lastBalanceRefreshAt'] = now
        s['lastAccountDiscoveryAt'] = now
        s.pop('balanceRefreshErrorKind', None)
        s['newAccountsLastSeenCount'] = len(discovered)
        if discovered:
            s['lastNewAccountsAt'] = now
            # The owner requested automatic monitoring of new institutions.
            # Explicitly excluded/disabled accounts remain excluded.
            monitored = set(s.get('monitoredAccounts', []))
            monitored.update(a['key'] for a in discovered if syncing(s, a))
            s['monitoredAccounts'] = sorted(monitored)
            s['nextSyncAt'] = min(s.get('nextSyncAt', now), now + 3600)
            # One scoped initial transaction request for all newly discovered
            # accounts when capacity remains. Existing account rows are intact.
            remaining, _ = _remaining_discovery_budget(s, now)
            selected = [a for a in discovered if syncing(s, a)]
            if selected and remaining > AUTOMATIC_REQUEST_RESERVE:
                claim_request(s, now)
                tag = write_state(uid, s, tag)
                initial = get_data(s['access'], selected)
                selected_keys = {a['key'] for a in selected}
                scoped = {**initial, 'accounts': [
                    a for a in initial.get('accounts', [])
                    if account_key(a) in selected_keys]}
                if scoped['accounts']:
                    before_missing = copy.deepcopy(s.get('missingAccounts', []))
                    candidate = merge(copy.deepcopy(s), scoped, now)
                    candidate['missingAccounts'] = before_missing
                    for account in candidate['accounts']:
                        if account['key'] in selected_keys and any(
                            not t.get('pending', False)
                            for a in scoped['accounts'] if account_key(a)==account['key']
                            for t in a.get('transactions', [])):
                            account['firstSyncAt'] = now
                            account.pop('discovered', None)
                    s = candidate
    except HTTPException as exc:
        # Retain the saved ledger. A temporary balance refresh failure alone
        # does not claim every institution needs reconnection.
        s['balanceRefreshErrorKind'] = exc.status_code
    return finish(uid, s, tag)

def sync(uid):
    s, tag = reserve(uid, 'sync')
    if not s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'Connect SimpleFIN first.')
    now = int(time.time())
    if now < s.get('nextSyncAt', 0):
        return finish(uid, s, tag)
    # Linking authorizes read-only activity for every returned account. No separate toggle.
    s['monitoredAccounts'] = sorted({a['key'] for a in s['accounts'] if syncing(s, a)})
    watched = set(s['mappings']) | set(s['monitoredAccounts'])
    selected = [a for a in s['accounts'] if a['key'] in watched and syncing(s, a)]
    if not selected:
        return finish(uid, s, tag)
    s['lastAttempt'] = now
    # Reserve the next attempt before network I/O. Restart/timeout cannot exhaust provider quotas.
    s['nextSyncAt'] = now + FULL_SYNC_INTERVAL
    s['nextBalanceRefreshAt'] = now + 3600
    try:
        claim_request(s, now)
        tag = write_state(uid, s, tag)
        candidate = merge(copy.deepcopy(s), get_data(s['access'], selected), now)
        # Optional, bounded Groq classification occurs only after successful
        # provider data retrieval; it never writes raw activity or milestones.
        try:
            classify_unknown(candidate, now=now)
        except Exception as error:
            BACKGROUND_LOGGER.warning('finance_category_error class=%s', type(error).__name__)
        s = candidate
        s.pop('error', None)
        set_provider_status(s)
    except HTTPException as e:
        s['status'] = 'attention'
        s['error'] = e.detail
        attempts = min(s.get('syncFailureAttempts', 0) + 1, 4)
        s['syncFailureAttempts'] = attempts
        s['nextSyncAt'] = now + ((1, 3, 6)[attempts-1] * 3600 if attempts <= 3 else DAY)
        s['health'] = {**s.get('health', {}), 'lastError': type(e).__name__, 'nextRetry': s['nextSyncAt']}
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
    balanceModeUserSet: bool | None = None
    hidden: bool | None = None
    displayName: str | None = Field(default=None, max_length=90)
    includeSpending: bool | None = None
    syncEnabled: bool | None = None

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


@router.get('/history/{month}')
def history_month(month: str, request: Request):
    uid = owner(request)
    from finance_history_archive import archive_key
    try:
        archive_key('simplefin', uid, month)
    except ValueError:
        raise HTTPException(400, 'Choose a valid month.')
    archived = read_month(store(), cipher(), 'simplefin', uid, month)
    state, _etag = read_state(uid)
    # Apply the same public-id hashing and category rules as recent activity.
    # Never send raw bridge transaction identifiers back to the browser.
    state['transactions'] = archived
    public = public_state(state)
    return {'month': month, 'transactions': public.get('transactions', {})}


@router.get('')
def status(request: Request):
    uid = owner(request)
    try:
        cipher()
    except HTTPException:
        return {'configured': False, 'connected': False, 'evidenceOnly': True}
    state, etag = read_state(uid)
    if reclassify_stored_warnings(state):
        try:
            # Conditional encrypted write makes the migration durable exactly once.
            write_state(uid, state, etag)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # Another writer won; never replace its newer account state.
            state, _ = read_state(uid)
            reclassify_stored_warnings(state)
    return public_state(state)


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
        claim_request(s, int(time.time()))
        tag = write_state(uid, s, tag)
        data = get_data(access, balances=True)
        s['accounts'] = [{**a,'firstSeenAt':int(time.time())} for a in normalize_accounts(data)]
        s['monitoredAccounts'] = sorted(a['key'] for a in s['accounts'])
        observed = int(time.time())
        record_balances(s, s['accounts'], observed)
        # Reserve the daily provider budget before automatic first import.
        if s['accounts']:
            s['lastAttempt'], s['nextSyncAt'] = observed, observed + FULL_SYNC_INTERVAL
            s['nextBalanceRefreshAt'] = observed + BALANCE_SYNC_INTERVAL
            tag = write_state(uid, s, tag)
            try:
                claim_request(s, observed)
                tag = write_state(uid, s, tag)
                s = merge(copy.deepcopy(s), get_data(access, s['accounts']), observed)
                s.pop('error', None)
                set_provider_status(s)
            except HTTPException as activity_error:
                s['error'] = activity_error.detail
                s['status'] = 'attention'
        update_provider_notices(s, data)
        set_provider_status(s)
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


@router.post('/auto-refresh')
def auto_refresh_accounts(request: Request):
    """Zero-click Bridge return: same credential, budget, lease and owner scope."""
    uid = owner(request)
    view = refresh_balances(uid, force=True)
    state, _ = read_state(uid)
    remaining, reset = _remaining_discovery_budget(state, int(time.time()))
    return {**view, 'budgetRemaining': remaining, 'budgetResetAt': reset}


@router.post('/discover-guided')
def discover_guided(request: Request):
    """One-time return-to-app check, bounded by a real rolling request budget."""
    return discover(request, guided=True)


def _remaining_discovery_budget(state, now):
    recent = sorted(at for at in state.get('requestAttempts', [])
                    if isinstance(at, (int, float)) and at > now - DAY)
    return max(0, 24-len(recent)), (int(recent[0])+DAY if len(recent)>=24 else None)


@router.post('/discover-now')
def discover_now(request: Request):
    """Bank-connection return check. Uses existing credential, lease and request quota."""
    uid=owner(request)
    s,tag=reserve(uid, 'discover-now')
    now=int(time.time())
    if not s.get('access'):
        finish(uid,s,tag)
        raise HTTPException(409,'Connect SimpleFIN first.')
    remaining,limit_reset=_remaining_discovery_budget(s,now)
    if now<s.get('newBankDiscoveryNextAt',0):
        result=finish(uid,s,tag)
        result.update(found=False,newAccounts=0,bank='',discoveredAccounts=[],
                      nextRetryAt=s['newBankDiscoveryNextAt'],budgetRemaining=remaining)
        return result
    if remaining==0:
        finish(uid,s,tag)
        raise HTTPException(429, f'Daily limit reached; updates resume at {limit_reset}.')
    try:
        claim_request(s,now)
        # Reserve budget before network I/O; even a failed provider attempt costs a call.
        s['newBankDiscoveryNextAt']=now+90
        tag=write_state(uid,s,tag)
        payload=get_data(s['access'],balances=True)
        normalized=normalize_accounts(payload)
        if not normalized and s.get('accounts'):
            # Empty provider payload is not evidence of deletion or a successful
            # discovery. No source rows are changed.
            raise HTTPException(502,'SimpleFIN returned no accounts. Existing accounts were preserved.')
        old={a['key']:a for a in s.get('accounts',[])}
        added=[]
        for a in normalized:
            prior=old.get(a['key'])
            if prior:
                # Discovery must never override the identity, owner-selected
                # profile, account setting or historical metadata of a saved row.
                old[a['key']]={**prior}
            else:
                a['firstSeenAt']=now
                a['discovered']=True
                old[a['key']]=a
                added.append(a)
        s['accounts']=list(old.values())
        record_balances(s,normalized,now)
        s.pop('newBankDiscoveryError',None)
        result=finish(uid,s,tag)
        remaining,_=_remaining_discovery_budget(s,now)
        discovered=[a for a in result['accounts'] if a['key'] in {x['key'] for x in added}]
        banks=sorted({a.get('bank') or 'Bank' for a in discovered})
        result.update(found=bool(added),newAccounts=len(added),
                      bank=banks[0] if len(banks)==1 else '',
                      banks=banks,discoveredAccounts=discovered,
                      nextRetryAt=s['newBankDiscoveryNextAt'],budgetRemaining=remaining)
        return result
    except HTTPException as exc:
        s['newBankDiscoveryError']=exc.status_code
        finish(uid,s,tag)
        raise


class FirstDiscoverySync(BaseModel):
    accounts: list[str] = Field(min_length=1,max_length=500)


@router.post('/first-sync')
def first_discovery_sync(body: FirstDiscoverySync,request: Request):
    """One first-history request after the owner explicitly saves Sync ON."""
    uid=owner(request)
    s,tag=reserve(uid,'first-sync')
    now=int(time.time())
    if not s.get('access'):
        finish(uid,s,tag)
        raise HTTPException(409,'Connect SimpleFIN first.')
    selected=set(body.accounts)
    permitted={a['key']:a for a in s.get('accounts',[]) if a.get('discovered') and
               not a.get('firstSyncAt') and syncing(s,a)}
    if len(selected)!=len(body.accounts) or any(not re.fullmatch(r'[0-9a-f]{64}',k) for k in selected):
        finish(uid,s,tag)
        raise HTTPException(400,'Choose unique discovered accounts.')
    enabled=[permitted[k] for k in body.accounts if k in permitted and k in s.get('monitoredAccounts',[])]
    if not enabled:
        response=finish(uid,s,tag)
        response.update(syncStatus='not_enabled',budgetRemaining=_remaining_discovery_budget(s,now)[0])
        return response
    remaining,reset=_remaining_discovery_budget(s,now)
    if now < s.get('firstSyncNextAt',0):
        response=finish(uid,s,tag)
        response.update(syncStatus='waiting',reason='history_pending',
                        nextRetryAt=s['firstSyncNextAt'],budgetRemaining=remaining)
        return response
    if not remaining:
        s['firstSyncNextAt']=reset
        response=finish(uid,s,tag)
        response.update(syncStatus='waiting',reason='budget_exhausted',
                        nextRetryAt=reset,budgetRemaining=0)
        return response
    try:
        claim_request(s,now)
        tag=write_state(uid,s,tag)
        data=get_data(s['access'],enabled)
        if not data.get('accounts'):
            raise HTTPException(502,'SimpleFIN returned no new-account activity.')
        # The response is scoped to the selected new account IDs only.
        allowed={a['key'] for a in enabled}
        filtered={**data,'accounts':[a for a in data['accounts'] if account_key(a) in allowed]}
        if not filtered['accounts']:
            raise HTTPException(502,'New-account transaction data is not ready.')
        if not any(isinstance(t,dict) and not t.get('pending',False)
                   for a in filtered['accounts'] for t in a.get('transactions',[])):
            # An empty or pending-only response is not a successful first
            # history import. Defer without inventing posted transactions.
            s['firstSyncNextAt']=now+3600
            response=finish(uid,s,tag)
            response.update(syncStatus='waiting',reason='history_pending',
                            nextRetryAt=s['firstSyncNextAt'],
                            budgetRemaining=_remaining_discovery_budget(s,now)[0])
            return response
        # merge() normally computes missingAccounts across every watched account.
        # Scoped first-history reads are not evidence that old accounts vanished.
        prior_missing=s.get('missingAccounts',[])
        candidate=merge(copy.deepcopy(s),filtered,now)
        candidate['missingAccounts']=prior_missing
        for a in candidate['accounts']:
            if a['key'] in allowed:
                a['firstSyncAt']=now
                a.pop('discovered',None)
        s=candidate
        response=finish(uid,s,tag)
        response.update(syncStatus='synced',newAccounts=len(enabled),
                        budgetRemaining=_remaining_discovery_budget(s,now)[0])
        return response
    except HTTPException as exc:
        s['firstSyncErrorKind']=exc.status_code
        finish(uid,s,tag)
        raise


@router.post('/discover')
def discover(request: Request, guided: bool = False):
    uid = owner(request)
    s, tag = reserve(uid, 'discover')
    now = int(time.time())
    if not s.get('access'):
        finish(uid, s, tag)
        raise HTTPException(409, 'Connect SimpleFIN first.')
    if guided:
        recent = [at for at in s.get('guidedDiscoveryAttempts', [])
                  if isinstance(at, int) and at > now - DAY]
        hour = [at for at in recent if at > now - 3600]
        if (now < s.get('nextGuidedDiscoveryAt', 0) or len(hour) >= 3
                or len(recent) >= 6 or len(s.get('requestAttempts', [])) >= 24):
            s['nextGuidedDiscoveryAt'] = max(s.get('nextGuidedDiscoveryAt',0),
                hour[0] + 3600 if len(hour)>=3 else 0,
                recent[0] + DAY if len(recent)>=6 else 0)
            return finish(uid, s, tag)
        s['guidedDiscoveryAttempts'] = recent + [now]
        s['nextGuidedDiscoveryAt'] = now + 180
    elif now < s.get('nextDiscoveryAt', 0):
        return finish(uid, s, tag)
    s['nextDiscoveryAt'] = now + DAY
    tag = write_state(uid, s, tag)
    try:
        claim_request(s, now)
        tag = write_state(uid, s, tag)
        data = get_data(s['access'], balances=True)
        fresh = normalize_accounts(data)
        observed_accounts = {a['key']: a for a in s['accounts']}
        for a in fresh:
            a['firstSeenAt'] = observed_accounts.get(a['key'],{}).get('firstSeenAt', now)
        watched = set(s['mappings']) | set(s.get('monitoredAccounts', []))
        s['missingAccounts'] = sorted(k for k in watched if k not in {a['key'] for a in fresh})
        current = {a['key']: a for a in fresh}
        # Preserve missing matched accounts so an omitted response cannot silently delete a match.
        for a in s['accounts']:
            if (a['key'] in watched or a['key'] in s.get('accountExclusions', {})) and a['key'] not in current:
                fresh.append(a)
        s['accounts'] = fresh
        s['monitoredAccounts'] = sorted({a['key'] for a in fresh if syncing(s, a)})
        s.pop('error', None)
        record_balances(s, [a for a in fresh if a['key'] in current and syncing(s, a)], now)
        if any(syncing(s, a) for a in fresh) and now >= s.get('nextSyncAt', 0):
            s['lastAttempt'], s['nextSyncAt'] = now, now + FULL_SYNC_INTERVAL
            s['nextBalanceRefreshAt'] = now + BALANCE_SYNC_INTERVAL
            tag = write_state(uid, s, tag)
            try:
                claim_request(s, now)
                tag = write_state(uid, s, tag)
                s = merge(copy.deepcopy(s), get_data(s['access'], [a for a in fresh if syncing(s, a)]), now)
                s.pop('error', None)
                set_provider_status(s)
            except HTTPException as activity_error:
                s['error'] = activity_error.detail
                s['status'] = 'attention'
        update_provider_notices(s, data)
        set_provider_status(s)
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
    s['accountProfiles'] = merge_profiles(s.get('accountProfiles', {}), supplied)
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
            s.setdefault('merchantRules', {})[merchant_rule_key(body.account, name)] = {
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


def startup_discovery_due(state, now):
    """One automatic post-upgrade probe, never a failed-probe request storm."""
    return (not state.get('lastAccountDiscoveryAt')
            and now >= state.get('autoStartupRetryAt', 0))


async def scheduled_simplefin_loop():
    while True:
        try:
            if os.getenv('SIMPLEFIN_ENCRYPTION_KEY') and os.getenv('AUTHORIZED_USER_ID'):
                uid = hashlib.sha256(os.environ['AUTHORIZED_USER_ID'].strip().encode()).hexdigest()
                s, _ = await run_in_threadpool(read_state, uid)
                if s.get('access'):
                    # Keep several daily calls available for initial history
                    # and recovery. Zero-click startup scans legacy linked
                    # accounts immediately after this additive release.
                    remaining, _ = _remaining_discovery_budget(s, int(time.time()))
                    if remaining > AUTOMATIC_REQUEST_RESERVE:
                        initial_scan=startup_discovery_due(s, int(time.time()))
                        if initial_scan:
                            await run_in_threadpool(refresh_balances, uid, force=True)
                        elif (s.get('accounts') and
                              any(syncing(s, a) for a in s['accounts']) and
                              time.time() >= s.get('nextSyncAt', 0)):
                            await run_in_threadpool(sync, uid)
                        elif time.time() >= s.get('nextBalanceRefreshAt', 0):
                            await run_in_threadpool(refresh_balances, uid)
        except Exception as error:
            # Never log repr(error) or provider payload; both can contain secrets.
            BACKGROUND_LOGGER.warning("simplefin_scheduled_sync_error class=%s", type(error).__name__)
        await asyncio.sleep(61)


@router.post('/account-action')
def manage_account(body: AccountAction, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'account-action')
    try:
        result = account_action(s, s['accounts'], body)
        if result.get('delete'):
            purge_account(store(), cipher(), 'simplefin', uid, s.get('historyMonths', []), body.account)
            purge_history(s, body.account)
    except HTTPException:
        finish(uid, s, tag)
        raise
    return {**finish(uid, s, tag), 'accountAction': result}


class InstitutionAction(BaseModel):
    handle: str = Field(pattern=r'^[a-f0-9]{64}$')
    restore: bool = False
    deleteHistory: bool = False


@router.post('/institution-action')
def manage_institution(body: InstitutionAction, request: Request):
    uid = owner(request)
    s, tag = reserve(uid, 'institution-action')
    accounts = [a for a in s['accounts'] if institution_handle(a) == body.handle]
    if not accounts:
        finish(uid, s, tag)
        raise HTTPException(404, 'Bank not found.')
    excluded = s.setdefault('institutionExclusions', {})
    if body.restore:
        excluded.pop(body.handle, None)
        for a in accounts:
            s.setdefault('accountExclusions', {}).pop(a['key'], None)
    else:
        excluded[body.handle] = {'removedAt': int(time.time())}
        for a in accounts:
            account_action(s, s['accounts'], AccountAction(account=a['key'], action='remove', deleteHistory=body.deleteHistory))
            if body.deleteHistory:
                purge_account(store(), cipher(), 'simplefin', uid, s.get('historyMonths', []), a['key'])
                purge_history(s, a['key'])
    return finish(uid, s, tag)

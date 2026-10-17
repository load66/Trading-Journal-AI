"""Plaid Trial fallback: immutable team-wide quota, private credentials, no paid upgrade.

NEVER call Plaid Production-access application/upgrade endpoints: applying for
full Production access permanently removes Trial eligibility. This module uses
only the free Trial's permitted link and Item operations, and stays disabled
until an operator has verified the Plaid Dashboard's lifetime Item count.
"""
import hashlib
import json
import os
import re
import time
import uuid

import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from bonus_backups import owner, store
from bonus_simplefin import cipher

CAP = 10
LEDGER_KEY = "bonus-tracker-plaid-trial/v1/global.enc"
BASE = "https://production.plaid.com"
TTL = 4 * 3600
router = APIRouter(prefix="/api/bonus-tracker/plaid")


def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _ready():
    return (os.getenv("PLAID_TRIAL_ENABLED") == "true"
            and os.getenv("PLAID_TRIAL_PLAN_VERIFIED") == "true"
            and os.getenv("PLAID_TRIAL_BASELINE_CONFIRMED") == "true"
            and os.getenv("PLAID_TRIAL_PIPELINE_READY") == "true"
            and bool(os.getenv("PLAID_CLIENT_ID")) and bool(os.getenv("PLAID_SECRET")))


def _baseline():
    try:
        value = int(os.environ["PLAID_TRIAL_PRIOR_ITEMS"])
    except (KeyError, ValueError):
        raise HTTPException(503, "Verify the lifetime Item count in Plaid Dashboard before enabling fallback.")
    if not 0 <= value <= CAP:
        raise HTTPException(503, "Plaid Trial baseline is invalid.")
    return value


def _read():
    s = store()
    try:
        response = s.client.get_object(Bucket=s.bucket, Key=LEDGER_KEY)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None, None
        raise HTTPException(503, "Plaid quota ledger is temporarily unavailable.") from None
    try:
        raw = response["Body"].read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise ValueError("too large")
        doc = json.loads(cipher().decrypt(raw))
        if (not isinstance(doc, dict) or doc.get("version") != 1
                or type(doc.get("used")) is not int or not 0 <= doc["used"] <= CAP
                or not isinstance(doc.get("holds"), dict)
                or not isinstance(doc.get("items"), dict)):
            raise ValueError("invalid ledger")
        return doc, response["ETag"]
    except Exception:
        raise HTTPException(503, "Plaid quota ledger could not be verified. Linking is disabled.") from None


def _write(doc, etag):
    raw = cipher().encrypt(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())
    if len(raw) > 512 * 1024:
        raise HTTPException(503, "Plaid quota ledger requires maintenance.")
    s = store()
    kwargs = {"Bucket": s.bucket, "Key": LEDGER_KEY, "Body": raw,
              "ContentType": "application/octet-stream",
              **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"})}
    try:
        s.client.put_object(**kwargs)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in (
                "PreconditionFailed", "ConditionalRequestConflict", "412", "409"):
            raise HTTPException(409, "Plaid quota changed. Refresh and retry.") from None
        raise HTTPException(503, "Plaid quota could not be saved. Linking is disabled.") from None
    observed, new_tag = _read()
    if observed != doc:
        raise HTTPException(503, "Plaid quota readback failed. Linking is disabled.")
    return new_tag


def _ledger():
    if not _ready():
        raise HTTPException(503, "Plaid Trial is not activated. Use SimpleFIN or manual entry until data monitoring is ready.")
    doc, tag = _read()
    if doc is None:
        doc = {"version": 1, "used": _baseline(), "holds": {}, "items": {}}
        tag = _write(doc, None)
    return doc, tag


def _prune(doc):
    """Never expire issued capacity automatically.

    Link creates an Item BEFORE public-token exchange. A lost callback, mobile
    navigation, or cancelled browser cannot prove that no Item was created.
    Only operator reconciliation against Plaid's lifetime usage may release an
    unused reservation; successful Item counts must never be decremented.
    https://plaid.com/docs/api/items/#itempublic_tokenexchange
    """
    return


def _capacity(doc, *, session=None):
    # Global quota belongs to the Plaid TEAM, not the currently logged-in owner.
    # Outstanding/uncertain exchanges consume capacity even when provider state is unknown.
    occupied = doc["used"] + len(doc["holds"])
    if doc["used"] >= CAP or occupied > CAP or (
            session is None and occupied >= CAP):
        raise HTTPException(409, "Plaid's 10 lifetime Trial slots are exhausted or reserved. No paid upgrade is permitted.")
    if session is not None and session not in doc["holds"]:
        raise HTTPException(409, "Plaid link session expired or was not reserved.")


def _provider(endpoint, body):
    if endpoint not in ("/link/token/create", "/item/public_token/exchange", "/item/remove"):
        raise HTTPException(500, "Unsupported Plaid operation.")
    if not _ready():
        raise HTTPException(503, "Plaid Trial is not configured.")
    try:
        response = httpx.post(
            BASE + endpoint,
            json={**body, "client_id": os.environ["PLAID_CLIENT_ID"],
                  "secret": os.environ["PLAID_SECRET"]},
            timeout=22, follow_redirects=False)
        if endpoint == "/item/remove" and response.status_code in (400, 404):
            # A prior completed revocation may be retried after a storage conflict.
            if response.json().get("error_code") == "ITEM_NOT_FOUND":
                return {"removed": True}
        if response.status_code != 200:
            # Never print provider payloads, tokens, or institution information.
            raise HTTPException(502, "Plaid rejected the request. Check Trial eligibility and link status.")
        return response.json()
    except httpx.HTTPError:
        raise HTTPException(502, "Plaid connection is unavailable. The reserved slot remains blocked for safety.") from None
    except ValueError:
        raise HTTPException(502, "Plaid returned an unsupported response.") from None


def _display(doc, uid):
    active = [
        {"institution": item.get("institution", "Financial institution"),
         "connectedAt": item.get("created", 0),
         "handle": hashlib.sha256((uid + "\0" + item_id).encode()).hexdigest()}
        for item_id, item in doc["items"].items()
        if item.get("owner") == uid and not item.get("disconnectedAt") and not item.get("disconnectingAt")
    ]
    return {"provider": "plaid", "priority": "fallback", "plan": "trial",
            "limit": CAP, "used": doc["used"], "remaining": max(0, CAP - doc["used"] - len(doc["holds"])),
            "reserved": len(doc["holds"]), "connected": active,
            "paidUpgradeAllowed": False,
            "warning": "Each new Plaid login permanently consumes one of 10 Trial slots. Try SimpleFIN first."}


@router.get("", dependencies=[Depends(_private)])
def status(request: Request):
    uid = owner(request)
    if not _ready():
        return {"provider": "plaid", "priority": "fallback", "plan": "trial",
                "configured": False, "used": None, "limit": CAP, "remaining": None,
                "paidUpgradeAllowed": False}
    doc, _ = _ledger()
    return {**_display(doc, uid), "configured": True}


class Start(BaseModel):
    institution: str = Field(min_length=2, max_length=100)
    triedSimplefin: bool
    understandsLifetimeSlot: bool
    allowDuplicate: bool = False


@router.post("/link-token", dependencies=[Depends(_private)])
def link_token(body: Start, request: Request):
    uid = owner(request)
    if not body.triedSimplefin or not body.understandsLifetimeSlot:
        raise HTTPException(400, "Try SimpleFIN first and explicitly acknowledge the permanent Plaid Trial slot.")
    institution = re.sub(r"\s+", " ", body.institution).strip()
    if len(institution) < 2:
        raise HTTPException(400, "Enter the institution name.")
    doc, tag = _ledger()
    _prune(doc)
    if not body.allowDuplicate and any(
            item.get("owner") == uid and item.get("institution", "").casefold() == institution.casefold()
            for item in doc["items"].values()):
        raise HTTPException(409, "This institution is already linked through Plaid. Re-linking consumes another permanent slot.")
    _capacity(doc)
    session = uuid.uuid4().hex
    doc["holds"][session] = {"owner": uid, "institution": institution,
                            "phase": "issued", "expires": int(time.time()) + TTL}
    tag = _write(doc, tag)  # reserve BEFORE contacting the provider
    try:
        result = _provider("/link/token/create", {
            "user": {"client_user_id": uid}, "client_name": "BonusTracker",
            "products": ["transactions"], "country_codes": ["US"],
            "language": "en"})
        token = result.get("link_token")
        if not isinstance(token, str) or not token:
            raise HTTPException(502, "Plaid did not supply a valid Link token.")
    except HTTPException:
        # Creating a link token does not create an Item; cancellation is safe.
        fresh, fresh_tag = _read()
        if fresh["holds"].get(session, {}).get("phase") == "issued":
            del fresh["holds"][session]
            _write(fresh, fresh_tag)
        raise
    return {"session": session, "linkToken": token,
            "warning": "Completing this link permanently consumes one of the 10 Plaid Trial slots."}


class Exchange(BaseModel):
    session: str = Field(pattern=r"^[a-f0-9]{32}$")
    publicToken: str = Field(min_length=8, max_length=1000, repr=False)


@router.post("/exchange", dependencies=[Depends(_private)])
def exchange(body: Exchange, request: Request):
    uid = owner(request)
    doc, tag = _ledger()
    hold = doc["holds"].get(body.session)
    if not hold or hold.get("owner") != uid or hold.get("phase") != "issued":
        raise HTTPException(409, "Plaid link session is invalid, expired, or already exchanged.")
    _capacity(doc, session=body.session)
    if hold.get("expires", 0) < time.time():
        raise HTTPException(409, "Plaid link session expired. Start again if capacity remains.")
    # Mark in flight BEFORE token exchange. Never retry automatically: a provider
    # timeout may have created a lifetime Item even without a response.
    hold["phase"] = "exchanging"
    _write(doc, tag)
    result = _provider("/item/public_token/exchange", {"public_token": body.publicToken})
    token, item_id = result.get("access_token"), result.get("item_id")
    if not isinstance(token, str) or not token or not isinstance(item_id, str) or not item_id:
        raise HTTPException(503, "Plaid exchange is ambiguous. The reserved slot remains blocked.")
    # Persist Item token ONLY in the encrypted, globally quota-limited R2 ledger.
    # A failure after a successful exchange leaves the durable hold blocked; it
    # must be manually reconciled, never silently released or retried.
    for _ in range(5):
        doc, tag = _read()
        hold = doc["holds"].get(body.session)
        if not hold or hold.get("owner") != uid or hold.get("phase") != "exchanging":
            raise HTTPException(503, "Plaid quota needs reconciliation. Do not start another link.")
        if item_id in doc["items"] or doc["used"] >= CAP:
            raise HTTPException(503, "Plaid duplicate or quota conflict. The slot remains blocked.")
        doc["items"][item_id] = {"owner": uid, "institution": hold["institution"],
                                "access": token, "created": int(time.time())}
        doc["used"] += 1  # ONLY after successful public-token exchange
        del doc["holds"][body.session]
        try:
            _write(doc, tag)
            return {"connected": True, **_display(doc, uid)}
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
    raise HTTPException(503, "Plaid quota write requires reconciliation. No automatic retry is allowed.")


def _owned_item(ledger, uid, handle):
    if not re.fullmatch(r"[0-9a-f]{64}", handle):
        raise HTTPException(404, "Connected bank not found.")
    matches = [(i, row) for i, row in ledger['items'].items() if row.get('owner') == uid
               and hashlib.sha256((uid + "\0" + i).encode()).hexdigest() == handle]
    if len(matches) != 1:
        raise HTTPException(404, "Connected bank not found.")
    return matches[0]


@router.post('/items/{handle}/link-token', dependencies=[Depends(_private)])
def update_link(handle: str, request: Request):
    """Existing Item only. No reservation, token exchange or lifetime counter change."""
    uid = owner(request)
    ledger, _tag = _ledger()
    _ident, item = _owned_item(ledger, uid, handle)
    if item.get('disconnectedAt') or item.get('disconnectingAt') or not item.get('access'):
        raise HTTPException(409, 'This bank has been disconnected.')
    result = _provider('/link/token/create', {
        'access_token': item['access'], 'client_name': 'BonusTracker',
        'language': 'en', 'country_codes': ['US'], 'user': {'client_user_id': uid},
        'update': {'account_selection_enabled': True}})
    token = result.get('link_token')
    if not isinstance(token, str) or not token:
        raise HTTPException(502, 'The bank reconnect window could not be opened.')
    return {'linkToken': token, 'existingItem': True}


@router.post('/items/{handle}/update-complete', dependencies=[Depends(_private)])
def update_complete(handle: str, request: Request):
    uid = owner(request)
    ledger, _tag = _ledger()
    item_id, item = _owned_item(ledger, uid, handle)
    if item.get('disconnectedAt') or item.get('disconnectingAt'):
        raise HTTPException(409, 'This bank has been disconnected.')
    from bonus_plaid_accounts import _read as read_activity, _write as write_activity, _provider as get, _merge, _public
    activity, tag = read_activity(uid)
    if activity.get('leaseUntil', 0) > time.time():
        raise HTTPException(409, 'A sync is in progress. Try again shortly.')
    raw = get('/accounts/get', item['access'])
    old = activity['items'].get(item_id, {})
    state = _merge(old, item_id, item['institution'], raw, None, int(time.time()))
    shared = {a['key'] for a in state.get('accounts', [])}
    from finance_account_management import AccountAction, action
    for a in old.get('accounts', []):
        if a['key'] not in shared:
            action(activity, old['accounts'], AccountAction(account=a['key'], action='remove'))
            state['accounts'].append(a)
    # Keep excluded account history, and restart the existing cursor safely for new shared accounts.
    if shared - {a['key'] for a in old.get('accounts', [])}:
        state['cursor'] = ''
    state['nextTransactionSyncAt'] = min(state.get('nextTransactionSyncAt', 0), int(time.time()))
    activity['items'][item_id] = state
    write_activity(uid, activity, tag)
    return _public(activity, ledger, uid)


@router.delete('/items/{handle}', dependencies=[Depends(_private)])
def disconnect_item(handle: str, request: Request, delete_history: bool = False):
    """Durable stop-before-revoke. Retries cannot reintroduce revoked access."""
    uid = owner(request)
    ledger, tag = _ledger()
    item_id, item = _owned_item(ledger, uid, handle)
    if item.get('disconnectedAt') and not item.get('cleanupPending'):
        return {'disconnected': True, **_display(ledger, uid)}
    from bonus_plaid_accounts import _read as read_activity, _write as write_activity
    activity, activity_tag = read_activity(uid)
    if activity.get('leaseUntil', 0) > time.time():
        raise HTTPException(409, 'The current bank sync must finish before disconnecting.')
    if not item.get('disconnectedAt') and not item.get('disconnectingAt'):
        item['disconnectingAt'] = int(time.time())
        item['retainHistory'] = not delete_history
        _write(ledger, tag)
    # This state is excluded from every foreground/background sync before revocation.
    if not item.get('disconnectedAt'):
        _provider('/item/remove', {'access_token': item['access']})
        current, current_tag = _read()
        current['items'][item_id] = {'owner': uid, 'institution': item['institution'],
            'created': item.get('created', 0), 'disconnectedAt': int(time.time()),
            'retainHistory': item.get('retainHistory', True), 'cleanupPending': True}
        _write(current, current_tag)
    else:
        current = ledger
    delete_history = not current['items'][item_id].get('retainHistory', True)
    if delete_history:
        cached = activity.get('items', {}).pop(item_id, {})
        keys = {account['key'] for account in cached.get('accounts', [])}
        from finance_history_archive import purge_account
        for account in keys:
            purge_account(store(), cipher(), 'plaid', uid, activity.get('historyMonths', []), account)
        activity['monitored'] = [k for k in activity.get('monitored', []) if k not in keys]
        for field in ('mappings', 'evidenceReviews', 'accountProfiles', 'transactionReviews'):
            activity[field] = {k: v for k, v in activity.get(field, {}).items() if k not in keys}
    else:
        from finance_account_management import AccountAction, action
        cached = activity.get('items', {}).get(item_id, {})
        for account in cached.get('accounts', []):
            action(activity, cached['accounts'], AccountAction(account=account['key'], action='remove'))
    write_activity(uid, activity, activity_tag)
    current, tag = _read()
    current['items'][item_id].pop('cleanupPending', None)
    _write(current, tag)
    return {'disconnected': True, **_display(current, uid)}

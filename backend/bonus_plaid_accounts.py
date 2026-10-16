"""Plaid Trial read-only adapter; no bank actions, upgrades, or provider tokens to clients.

Uses the existing Tracker evidence engine. All real provider access requires the
separately guarded Trial activation switches in bonus_plaid_trial.py.
"""
import copy
import asyncio
import hashlib
import json
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from bonus_backups import owner, store
from bonus_simplefin import (cipher, clean, classify, evidence_fingerprint, public_ref,
    public_state, apply_mapping_selection, apply_evidence_decision, SelectionBody,
    EvidenceReviewBody, AccountProfilesBody, TransactionReviewBody)
import bonus_plaid_trial as quota

DAY = 86400
MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 25
MAX_ROWS = 10000
router = APIRouter(prefix="/api/bonus-tracker/plaid")


def connection_handle(uid, item_id):
    return hashlib.sha256((uid + "\0" + item_id).encode()).hexdigest()


ROUTES = frozenset(("/accounts/get", "/transactions/sync"))


def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _key(uid):
    return "bonus-tracker-plaid-trial/v1/" + uid + "/activity.enc"


def _default():
    return {"version": 1, "items": {}, "monitored": [], "lastSuccess": None, "nextSyncAt": 0, "mappings": {},
            "evidenceReviews": {}, "accountProfiles": {}, "transactionReviews": {}}


def _read(uid):
    s = store()
    try:
        r = s.client.get_object(Bucket=s.bucket, Key=_key(uid))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return _default(), None
        raise HTTPException(503, "Plaid private activity is unavailable.") from None
    try:
        raw = r["Body"].read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        doc = json.loads(cipher().decrypt(raw))
        if (not isinstance(doc, dict) or doc.get("version") != 1
                or not isinstance(doc.get("items"), dict)
                or not isinstance(doc.get("monitored"), list)):
            raise ValueError()
        return doc, r["ETag"]
    except Exception:
        raise HTTPException(503, "Plaid activity integrity check failed. Sync disabled.") from None


def _write(uid, doc, etag):
    raw = cipher().encrypt(json.dumps(doc, separators=(",", ":"), sort_keys=True).encode())
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "Plaid activity exceeds its safe storage limit.")
    s = store()
    args = {"Bucket": s.bucket, "Key": _key(uid), "Body": raw,
            "ContentType": "application/octet-stream",
            **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"})}
    try:
        s.client.put_object(**args)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict", "409", "412"):
            raise HTTPException(409, "Plaid activity changed. Refresh before retrying.") from None
        raise HTTPException(503, "Plaid activity could not be saved.") from None
    seen, new_tag = _read(uid)
    if seen != doc:
        raise HTTPException(503, "Plaid activity save could not be verified.")
    return new_tag


def _provider(endpoint, token, **kwargs):
    # Read-only allowlist; NEVER add /item/remove, /transfer, /bank_transfer,
    # production-access requests, or any other billable/financial action.
    if endpoint not in ROUTES:
        raise HTTPException(500, "Unsupported read-only Plaid operation.")
    if not quota._ready():
        raise HTTPException(503, "Plaid fallback is not activated.")
    try:
        response = httpx.post(quota.BASE + endpoint,
            json={"client_id": quota.os.environ["PLAID_CLIENT_ID"],
                  "secret": quota.os.environ["PLAID_SECRET"],
                  "access_token": token, **kwargs},
            timeout=25, follow_redirects=False)
        if response.status_code != 200:
            # Never log raw request/response bodies: they may contain bank data.
            raise HTTPException(502, "Plaid could not return account activity.")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Plaid read-only sync is temporarily unavailable.") from None


def _money(v):
    if v is None:
        return None
    try:
        x = Decimal(str(v))
        if not x.is_finite() or abs(x) > 10**12:
            raise ValueError()
        # Canonical text ensures equal provider values have equal fingerprints.
        if x == 0:
            return "0"
        return format(x.normalize(), "f")
    except (ValueError, InvalidOperation):
        raise HTTPException(502, "Provider supplied invalid money data.") from None


def _account_key(item_id, raw_id):
    return hashlib.sha256(("plaid\0" + item_id + "\0" + raw_id).encode()).hexdigest()


def _account(item_id, institution, row, now):
    if not isinstance(row, dict) or not isinstance(row.get("account_id"), str) or not row["account_id"]:
        raise HTTPException(502, "Provider account identity is invalid.")
    balance = row.get("balances") or {}
    if not isinstance(balance, dict):
        raise HTTPException(502, "Provider account balance is invalid.")
    raw = row["account_id"]
    return {"id": raw, "key": _account_key(item_id, raw),
            "name": clean(row.get("name") or "Financial account", 90),
            "bank": clean(institution, 100),
            "currency": clean(balance.get("iso_currency_code") or
                               balance.get("unofficial_currency_code"), 8),
            "balance": _money(balance.get("current")),
            "availableBalance": _money(balance.get("available")),
            # /accounts/get balances are potentially cached by Plaid. Do not
            # invent a provider freshness timestamp when one is not supplied.
            "balanceDate": None, "observedAt": now,
            "provider": "plaid",
            "accountType": clean(row.get("type"), 32),
            "accountSubtype": clean(row.get("subtype"), 40)}


def _posted(text):
    try:
        parsed = date.fromisoformat(text)
        if parsed.isoformat() != text:
            raise ValueError()
        return int(datetime(parsed.year, parsed.month, parsed.day, 12, tzinfo=timezone.utc).timestamp())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(502, "Provider transaction date is invalid.") from None


def _transaction(row, now):
    if not isinstance(row, dict):
        raise HTTPException(502, "Provider transaction data is invalid.")
    ident = row.get("transaction_id")
    account = row.get("account_id")
    if not isinstance(ident, str) or not ident or not isinstance(account, str) or not account:
        raise HTTPException(502, "Provider transaction identity is invalid.")
    debit = _money(row.get("amount"))
    if debit is None:
        raise HTTPException(502, "Provider transaction amount is unavailable.")
    # Plaid-positive means debit, unlike SimpleFIN-positive which means credit.
    amount = _money(-Decimal(debit))
    # Prefer bank-provided transaction wording over Plaid merchant normalization:
    # payroll, transfer and fee evidence may be lost in a shortened merchant label.
    description = clean(row.get("original_description") or row.get("name") or row.get("merchant_name"), 450)
    pending = row.get("pending") is True
    return {"id": ident, "account": account, "posted": _posted(row.get("date")),
            "amount": amount, "description": description,
            "pending": pending, "kind": classify(amount, description),
            "currency": clean(row.get("iso_currency_code") or
                              row.get("unofficial_currency_code"), 8),
            "observedAt": now, "lastObservedAt": now}


def _sync_item(token, current_cursor):
    """Apply all pages atomically, never persist a partial cursor."""
    cursor = current_cursor or ""
    added, modified, removed = {}, {}, set()
    for _ in range(MAX_PAGES):
        response = _provider("/transactions/sync", token, cursor=cursor, count=500)
        if not isinstance(response, dict):
            raise HTTPException(502, "Plaid returned invalid transaction updates.")
        new_cursor, more = response.get("next_cursor"), response.get("has_more")
        if not isinstance(new_cursor, str) or type(more) is not bool:
            raise HTTPException(502, "Plaid sync pagination is invalid.")
        for kind, destination in (("added", added), ("modified", modified)):
            rows = response.get(kind)
            if not isinstance(rows, list) or len(rows) > 500:
                raise HTTPException(502, "Plaid transaction page is invalid.")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("transaction_id"), str):
                    raise HTTPException(502, "Plaid transaction identity is invalid.")
                destination[row["transaction_id"]] = row
        removals = response.get("removed")
        if not isinstance(removals, list):
            raise HTTPException(502, "Plaid removals are invalid.")
        for row in removals:
            if not isinstance(row, dict) or not isinstance(row.get("transaction_id"), str):
                raise HTTPException(502, "Plaid removal identity is invalid.")
            removed.add(row["transaction_id"])
        if len(added) + len(modified) + len(removed) > MAX_ROWS:
            raise HTTPException(502, "Plaid transaction batch is too large.")
        cursor = new_cursor
        if not more:
            return added, modified, removed, cursor
    raise HTTPException(502, "Plaid sync exceeds the safe page limit. Existing data is preserved.")


def _merge(old, item_id, name, provider_accounts, changes, now):
    if not isinstance(provider_accounts, dict) or not isinstance(provider_accounts.get("accounts"), list):
        raise HTTPException(502, "Plaid accounts are unavailable.")
    rows = provider_accounts["accounts"]
    if len(rows) > 500:
        raise HTTPException(502, "Plaid account count is invalid.")
    accounts = [_account(item_id, name, row, now) for row in rows]
    if len(set(a["key"] for a in accounts)) != len(accounts):
        raise HTTPException(502, "Plaid account identities are duplicated.")
    out = copy.deepcopy(old) if isinstance(old, dict) else {}
    out["accounts"] = accounts
    if changes is None:
        out.setdefault("transactions", {})
        out.setdefault("cursor", "")
        return out
    added, modified, removed, cursor = changes
    transactions = copy.deepcopy(out.get("transactions", {}))
    for ident in removed:
        transactions.pop(ident, None)
    for ident, row in {**added, **modified}.items():
        # A posted transaction may replace an earlier pending authorization
        # under a different ID. Remove only the provider-linked predecessor.
        prior_pending = row.get("pending_transaction_id") if isinstance(row, dict) else None
        if isinstance(prior_pending, str) and prior_pending != ident:
            previous = transactions.get(prior_pending)
            if isinstance(previous, dict) and previous.get("pending") and previous.get("account") == row.get("account_id"):
                transactions.pop(prior_pending, None)
        converted = _transaction(row, now)
        existing = transactions.get(ident)
        if existing:
            converted["observedAt"] = existing.get("observedAt", now)
            if evidence_fingerprint(existing) != evidence_fingerprint(converted):
                converted["changedAt"] = now
                converted["revisions"] = (existing.get("revisions", []) +
                   [{k: existing.get(k) for k in ("posted", "amount", "description", "pending")}])[-5:]
            else:
                for k in ("changedAt", "revisions"):
                    if k in existing:
                        converted[k] = existing[k]
        transactions[ident] = converted
    cutoff = now - 90 * DAY
    # Pending entries remain visible as pending, never in posted-only totals
    # or automatic bonus evidence. Plaid's removal/replacement delta controls
    # when a pending authorization disappears.
    out["transactions"] = {k: v for k, v in transactions.items()
                          if isinstance(v, dict) and v.get("posted", 0) >= cutoff}
    out["cursor"] = cursor
    out["lastTransactionAttemptAt"] = now
    out["lastTransactionSyncAt"] = now
    out["nextTransactionSyncAt"] = now + DAY
    out.pop("transactionSyncError", None)
    out["transactionSyncStatus"] = (
        "ready" if any(not x.get("pending") for x in out["transactions"].values())
        else "pending_only" if out["transactions"] else "awaiting_provider_data")
    return out


def _view(doc, ledger, uid):
    accessible = {item_id for item_id, row in ledger["items"].items()
                  if isinstance(row, dict) and row.get("owner") == uid and not row.get("disconnectedAt")}
    view = {"access": bool(accessible), "accounts": [], "transactions": {},
            "monitoredAccounts": list(doc.get("monitored", [])),
            **{key: copy.deepcopy(doc.get(key, {})) for key in
               ("mappings", "evidenceReviews", "accountProfiles", "transactionReviews")},
            "lastSuccess": doc.get("lastSuccess"), "nextSyncAt": doc.get("nextSyncAt"),
            "error": doc.get("error"), "status": "attention" if doc.get("error") else "connected"}
    permitted = set(doc.get("monitored", []))
    for item_id, state in doc.get("items", {}).items():
        if item_id not in accessible:
            continue
        for account in state.get("accounts", []):
            key = account["key"]
            view["accounts"].append({**{k: v for k, v in account.items() if k != "id"},
                                     "connectionHandle": connection_handle(uid, item_id),
                                     "transactionSyncStatus": state.get("transactionSyncStatus", "awaiting_first_sync"),
                                     "transactionLastSyncAt": state.get("lastTransactionSyncAt"),
                                     "transactionNextSyncAt": state.get("nextTransactionSyncAt", doc.get("nextSyncAt")),
                                     "transactionSyncError": state.get("transactionSyncError")})
            if key in permitted:
                view["transactions"][key] = {
                    raw: {k: v for k, v in row.items() if k not in ("id", "account")}
                    for raw, row in state.get("transactions", {}).items()
                    if row.get("account") == account["id"]}
    allowed = {a["key"] for a in view["accounts"]}
    view["monitoredAccounts"] = [key for key in view["monitoredAccounts"] if key in allowed]
    for field in ("mappings", "evidenceReviews", "accountProfiles", "transactionReviews"):
        view[field] = {key: value for key, value in view[field].items() if key in allowed}
    # A category correction, like an evidence review, is bound to its revision.
    view["transactionReviews"] = {
        key: {raw: review for raw, review in rows.items()
              if review.get("fingerprint") == evidence_fingerprint(
                  view["transactions"].get(key, {}).get(raw, {}))
              and raw in view["transactions"].get(key, {})}
        for key, rows in view["transactionReviews"].items()}
    return view


def _public(doc, ledger, uid):
    result = public_state(_view(doc, ledger, uid))
    result.update(provider="plaid", authoritativeMilestones=False,
                  stale=not doc.get("lastSuccess") or time.time() - doc["lastSuccess"] > 2 * DAY)
    return result


def _editable(request):
    uid = owner(request)
    ledger, _ = quota._ledger()
    doc, etag = _read(uid)
    if doc.get("leaseUntil", 0) > time.time():
        raise HTTPException(409, "Plaid synchronization is busy. Retry later.")
    return uid, ledger, doc, etag, _view(doc, ledger, uid)


@router.put("/accounts/selection", dependencies=[Depends(_private)])
def select(body: SelectionBody, request: Request):
    uid, ledger, doc, etag, view = _editable(request)
    allowed = {a["key"] for a in view["accounts"]}
    if not set(body.mappings).issubset(allowed):
        raise HTTPException(400, "Choose only your discovered Plaid accounts.")
    if not set(body.mappings).issubset(set(doc.get("monitored", []))):
        raise HTTPException(400, "Enable transaction monitoring before linking a bonus.")
    apply_mapping_selection(view, {key: value.model_dump(exclude_none=True)
                                   for key, value in body.mappings.items()})
    doc["mappings"], doc["evidenceReviews"] = view["mappings"], view["evidenceReviews"]
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.put("/accounts/evidence-review", dependencies=[Depends(_private)])
def evidence_review(body: EvidenceReviewBody, request: Request):
    uid, ledger, doc, etag, view = _editable(request)
    apply_evidence_decision(view, body, public_state(view))
    doc["evidenceReviews"] = view["evidenceReviews"]
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.put("/accounts/account-profiles", dependencies=[Depends(_private)])
def profiles(body: AccountProfilesBody, request: Request):
    uid, ledger, doc, etag, view = _editable(request)
    if not set(body.profiles).issubset({a["key"] for a in view["accounts"]}):
        raise HTTPException(400, "Choose only your discovered Plaid accounts.")
    doc["accountProfiles"] = {key: value.model_dump() for key, value in body.profiles.items()}
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.put("/accounts/transaction-review", dependencies=[Depends(_private)])
def transaction_review(body: TransactionReviewBody, request: Request):
    uid, ledger, doc, etag, view = _editable(request)
    rows = view["transactions"].get(body.account, {})
    matches = [raw for raw in rows if public_ref(body.account, raw) == body.transaction]
    if len(matches) != 1:
        raise HTTPException(400, "Choose a saved transaction from this account.")
    raw = matches[0]
    reviews = doc.setdefault("transactionReviews", {}).setdefault(body.account, {})
    if body.category == "unreviewed":
        reviews.pop(raw, None)
    else:
        reviews[raw] = {"category": body.category, "reviewedAt": int(time.time()),
                        "fingerprint": evidence_fingerprint(rows[raw])}
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.get("/accounts", dependencies=[Depends(_private)])
def accounts(request: Request):
    uid = owner(request)
    if not quota._ready():
        return {"provider": "plaid", "configured": False, "connected": False,
                "accounts": [], "transactions": {}, "monitoredAccounts": [],
                "warning": "SimpleFIN remains primary. Plaid read-only monitoring is not activated."}
    ledger, _ = quota._ledger()
    doc, _ = _read(uid)
    return _public(doc, ledger, uid)


class Monitored(BaseModel):
    accounts: list[str] = Field(max_length=50)


@router.put("/accounts/monitoring", dependencies=[Depends(_private)])
def monitor(body: Monitored, request: Request):
    uid = owner(request)
    ledger, _ = quota._ledger()
    doc, etag = _read(uid)
    if doc.get("leaseUntil", 0) > time.time():
        raise HTTPException(409, "Plaid syncing is busy. Retry later.")
    all_accounts = {a["key"] for item_id, row in doc["items"].items()
                    if ledger["items"].get(item_id, {}).get("owner") == uid
                    for a in row.get("accounts", [])}
    requested = set(body.accounts)
    if (len(requested) != len(body.accounts) or not requested.issubset(all_accounts)
            or any(len(k) != 64 or any(c not in "0123456789abcdef" for c in k)
                   for k in requested)):
        raise HTTPException(400, "Select only your discovered Plaid accounts.")
    new_accounts = requested - set(doc.get("monitored", []))
    doc["monitored"] = list(body.accounts)
    for field in ("mappings", "evidenceReviews", "transactionReviews"):
        doc[field] = {key: value for key, value in doc.get(field, {}).items() if key in requested}
    # First selection may follow account-only discovery. Once Transactions was
    # attempted, preference changes must never reset its daily provider budget.
    if new_accounts and doc.get("transactionNextSyncAt", 0) <= time.time():
        doc["nextSyncAt"] = 0
    for item_id, state in doc["items"].items():
        # An account added to monitoring must not inherit an Item cursor that
        # skipped its older transactions while that account was unmonitored.
        if any(a.get("key") in new_accounts for a in state.get("accounts", [])):
            state["cursor"] = ""
        state["transactions"] = {k: v for k, v in state.get("transactions", {}).items()
                                 if _account_key(item_id, v.get("account", "")) in requested}
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.delete("/accounts/items/{handle}", dependencies=[Depends(_private)])
def disconnect_account_item(handle: str, request: Request):
    return quota.disconnect_item(handle, request)


@router.post("/accounts/sync", dependencies=[Depends(_private)])
def sync(request: Request):
    return sync_owner(owner(request))


def sync_owner(uid):
    ledger, _ = quota._ledger()
    known = {item_id: row for item_id, row in ledger["items"].items()
             if row.get("owner") == uid and not row.get("disconnectedAt")}
    if not known:
        raise HTTPException(409, "Connect a Plaid fallback institution first.")
    doc, etag = _read(uid)
    now = int(time.time())
    if doc.get("leaseUntil", 0) > now:
        raise HTTPException(409, "Plaid synchronization is busy.")
    if now < doc.get("nextSyncAt", 0):
        # The owner-wide daily budget protects *existing* Items. A just-linked
        # Item may still make its FIRST transaction request without re-querying
        # previously synced banks. This prevents a new OnePay bank showing an
        # account balance but no activity until an unrelated bank's next window.
        missing = {item_id: item for item_id, item in known.items()
                   if item_id not in doc.get("items", {})}
        can_discover = now >= doc.get("nextNewItemDiscoveryAt", 0)
        eligible_missing = missing if can_discover else {}
        first_sync = {item_id: item for item_id, item in known.items()
                      if item_id in doc.get("items", {}) and
                      doc["items"][item_id].get("accounts") and
                      not doc["items"][item_id].get("lastTransactionAttemptAt") and
                      not doc["items"][item_id].get("cursor") and
                      now >= doc["items"][item_id].get("nextTransactionSyncAt", 0)}
        if not eligible_missing and not first_sync:
            return _public(doc, ledger, uid)
        doc["lease"] = uuid.uuid4().hex
        doc["leaseUntil"] = now + 300
        if eligible_missing:
            doc["nextNewItemDiscoveryAt"] = now + DAY
        etag = _write(uid, doc, etag)
        candidate = copy.deepcopy(doc)
        try:
            for item_id, item in eligible_missing.items():
                raw = _provider("/accounts/get", item["access"])
                state = _merge({}, item_id, item["institution"], raw, None, now)
                candidate["monitored"] = sorted(set(candidate.get("monitored", [])) |
                    {a["key"] for a in state["accounts"]})
                if state["accounts"]:
                    try:
                        delta = _sync_item(item["access"], "")
                        state = _merge(state, item_id, item["institution"], raw, delta, now)
                        candidate["lastSuccess"] = now
                    except HTTPException as exc:
                        # Retain successful account discovery and clearly mark
                        # incomplete first-history coverage, never invent zero.
                        state["lastTransactionAttemptAt"] = now
                        state["nextTransactionSyncAt"] = now + DAY
                        state["transactionSyncStatus"] = "needs_attention"
                        state["transactionSyncError"] = exc.detail
                candidate["items"][item_id] = state
            for item_id, item in first_sync.items():
                old = candidate["items"][item_id]
                try:
                    delta = _sync_item(item["access"], old.get("cursor"))
                    candidate["items"][item_id] = _merge(
                        old, item_id, item["institution"],
                        {"accounts": [{
                            "account_id": a["id"], "name": a["name"],
                            "type": a.get("accountType"), "subtype": a.get("accountSubtype"),
                            "balances": {
                                "current": a.get("balance"), "available": a.get("availableBalance"),
                                "iso_currency_code": a.get("currency")}
                        } for a in old["accounts"]]}, delta, now)
                    candidate["lastSuccess"] = now
                except HTTPException as exc:
                    old["lastTransactionAttemptAt"] = now
                    old["nextTransactionSyncAt"] = now + DAY
                    old["transactionSyncStatus"] = "needs_attention"
                    old["transactionSyncError"] = exc.detail
            if eligible_missing:
                candidate["lastAccountDiscovery"] = now
            candidate.pop("error", None)
        except HTTPException as exc:
            # No partial encrypted writes or lost prior activity on failed
            # account discovery. Retry after the reserved provider interval.
            candidate = doc
            candidate["error"] = exc.detail
        candidate.pop("lease", None)
        candidate.pop("leaseUntil", None)
        _write(uid, candidate, etag)
        return _public(candidate, ledger, uid)
    doc["lease"] = uuid.uuid4().hex
        doc["leaseUntil"] = now + 300
        doc["nextNewItemDiscoveryAt"] = now + DAY
        etag = _write(uid, doc, etag)
        candidate = copy.deepcopy(doc)
        try:
            for item_id, item in missing.items():
                raw = _provider("/accounts/get", item["access"])
                candidate["items"][item_id] = _merge(
                    {}, item_id, item["institution"], raw, None, now)
                candidate["monitored"] = sorted(set(candidate.get("monitored", [])) |
                    {a["key"] for a in candidate["items"][item_id]["accounts"]})
            candidate["lastAccountDiscovery"] = now
            candidate.pop("error", None)
        except HTTPException as exc:
            # Never persist partially discovered Items or expose any provider
            # credentials. Fail once, then let the daily budget decide retry.
            candidate = doc
            candidate["error"] = exc.detail
        candidate.pop("lease", None)
        candidate.pop("leaseUntil", None)
        _write(uid, candidate, etag)
        return _public(candidate, ledger, uid)
    doc["lease"] = uuid.uuid4().hex
    doc["leaseUntil"] = now + 300
    doc["nextSyncAt"] = now + DAY  # reserve provider budget before remote call
    # Automatically linked accounts consume the same bounded daily transaction budget.
    doc["transactionNextSyncAt"] = now + DAY
    etag = _write(uid, doc, etag)
    candidate = copy.deepcopy(doc)
    try:
        monitored = set(candidate.get("monitored", []))
        for item_id, item in known.items():
            raw = _provider("/accounts/get", item["access"])
            old = candidate["items"].get(item_id, {})
            exposed = _merge(old, item_id, item["institution"], raw, None, now)
            discovered = {a["key"] for a in exposed["accounts"]}
            new_accounts = discovered - monitored
            monitored.update(discovered)
            candidate["monitored"] = sorted(monitored)
            if new_accounts and old.get("cursor"):
                # A preexisting cursor may have skipped activity for newly linked accounts.
                old = {**old, "cursor": ""}
            # Respect a newly linked Item's own first-sync budget. A daily
            # owner refresh must not issue a second request for that Item.
            if discovered and now >= old.get("nextTransactionSyncAt", 0):
                changes = _sync_item(item["access"], old.get("cursor"))
                exposed = _merge(old, item_id, item["institution"], raw, changes, now)
            # Plaid returns an Item-wide delta, even when only one account is
            # selected. Discard unselected activity before encrypted persistence.
            exposed["transactions"] = {
                ident: row for ident, row in exposed.get("transactions", {}).items()
                if _account_key(item_id, row.get("account", "")) in monitored
            }
            candidate["items"][item_id] = exposed
        # Permanently drop obsolete receipts, so a removed transaction cannot
        # silently regain approval if the provider later reuses its identifier.
        current = _view(candidate, ledger, uid)["transactions"]
        for field in ("evidenceReviews", "transactionReviews"):
            candidate[field] = {key: {raw: review for raw, review in rows.items()
                if raw in current.get(key, {}) and review.get("fingerprint") ==
                evidence_fingerprint(current[key][raw])}
                for key, rows in candidate.get(field, {}).items() if key in current}
        candidate["lastSuccess"] = now
        candidate.pop("error", None)
    except HTTPException as exc:
        # Fail closed: no partial cursor or transaction import is acknowledged.
        candidate = doc
        candidate["error"] = exc.detail
    candidate.pop("lease", None)
    candidate.pop("leaseUntil", None)
    _write(uid, candidate, etag)
    return _public(candidate, ledger, uid)


def scheduled_sync_once():
    if not quota._ready():
        return
    ledger, _ = quota._read()
    if not ledger:
        return
    owners = {row.get("owner") for row in ledger["items"].values() if row.get("owner")}
    for uid in owners:
        try:
            doc, _ = _read(uid)
            if time.time() >= doc.get("nextSyncAt", 0):
                sync_owner(uid)
        except Exception:
            # Private per-owner status reports failures; never log bank data.
            pass


async def scheduled_plaid_loop():
    while True:
        try:
            await asyncio.to_thread(scheduled_sync_once)
        except Exception:
            pass
        await asyncio.sleep(300)

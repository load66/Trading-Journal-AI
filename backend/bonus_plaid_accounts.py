"""Plaid Trial read-only adapter; no bank actions, upgrades, or provider tokens to clients.

This adapter is inactive until operator verification and integration with the
existing Tracker evidence engine. All real provider access requires the
separately guarded Trial activation switches in bonus_plaid_trial.py.
"""
import copy
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
from bonus_simplefin import cipher, clean, classify, evidence_fingerprint, automatic_category, merchant_name
import bonus_plaid_trial as quota

DAY = 86400
MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 25
MAX_ROWS = 10000
router = APIRouter(prefix="/api/bonus-tracker/plaid")
ROUTES = frozenset(("/accounts/get", "/transactions/sync"))


def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _key(uid):
    return "bonus-tracker-plaid-trial/v1/" + uid + "/activity.enc"


def _default():
    return {"version": 1, "items": {}, "monitored": [], "lastSuccess": None, "nextSyncAt": 0}


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
    out["transactions"] = {k: v for k, v in transactions.items()
                          if isinstance(v, dict) and v.get("posted", 0) >= cutoff
                          and not v.get("pending")}
    out["cursor"] = cursor
    return out


def _public(doc, ledger, uid):
    accessible = {item_id: row for item_id, row in ledger["items"].items()
                  if isinstance(row, dict) and row.get("owner") == uid}
    accounts, transactions = [], {}
    permitted = set(doc.get("monitored", []))
    for item_id, state in doc.get("items", {}).items():
        if item_id not in accessible:
            continue
        for account in state.get("accounts", []):
            key = account["key"]
            accounts.append({k: v for k, v in account.items() if k != "id"})
            if key in permitted:
                for row in state.get("transactions", {}).values():
                    if row.get("account") != account["id"]:
                        continue
                    opaque = hashlib.sha256((key + "\0" + row["id"]).encode()).hexdigest()
                    sanitized = {k: v for k, v in row.items() if k not in ("id", "account")}
                    sanitized["id"] = opaque
                    category, confidence, signals = automatic_category(row)
                    sanitized.update(category=category, confidence=confidence, signals=signals,
                                     categorySource="automatic", merchant=merchant_name(row.get("description", "")))
                    transactions.setdefault(key, {})[opaque] = sanitized
    return {"provider": "plaid", "evidenceOnly": True, "configured": True,
            "connected": bool(accessible), "accounts": accounts, "transactions": transactions,
            "monitoredAccounts": list(permitted), "lastSuccess": doc.get("lastSuccess"),
            "nextSyncAt": doc.get("nextSyncAt"), "error": doc.get("error"),
            "stale": not doc.get("lastSuccess") or time.time() - doc["lastSuccess"] > 2 * DAY,
            "authoritativeMilestones": False}


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
    doc["monitored"] = list(body.accounts)
    doc["nextSyncAt"] = 0
    for item_id, state in doc["items"].items():
        state["transactions"] = {k: v for k, v in state.get("transactions", {}).items()
                                 if _account_key(item_id, v.get("account", "")) in requested}
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.post("/accounts/sync", dependencies=[Depends(_private)])
def sync(request: Request):
    uid = owner(request)
    ledger, _ = quota._ledger()
    known = {item_id: row for item_id, row in ledger["items"].items()
             if row.get("owner") == uid}
    if not known:
        raise HTTPException(409, "Connect a Plaid fallback institution first.")
    doc, etag = _read(uid)
    now = int(time.time())
    if doc.get("leaseUntil", 0) > now:
        raise HTTPException(409, "Plaid synchronization is busy.")
    if now < doc.get("nextSyncAt", 0):
        return _public(doc, ledger, uid)
    doc["lease"] = uuid.uuid4().hex
    doc["leaseUntil"] = now + 300
    doc["nextSyncAt"] = now + DAY  # reserve provider budget before remote call
    etag = _write(uid, doc, etag)
    candidate = copy.deepcopy(doc)
    try:
        monitored = set(candidate.get("monitored", []))
        for item_id, item in known.items():
            raw = _provider("/accounts/get", item["access"])
            old = candidate["items"].get(item_id, {})
            exposed = _merge(old, item_id, item["institution"], raw, None, now)
            if monitored.intersection({a["key"] for a in exposed["accounts"]}):
                # Do not call Transactions for unmonitored accounts.
                changes = _sync_item(item["access"], old.get("cursor"))
                exposed = _merge(old, item_id, item["institution"], raw, changes, now)
            candidate["items"][item_id] = exposed
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

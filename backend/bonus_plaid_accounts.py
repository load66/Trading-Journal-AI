"""Plaid Trial read-only adapter; no bank actions, upgrades, or provider tokens to clients.

Uses the existing Tracker evidence engine. All real provider access requires the
separately guarded Trial activation switches in bonus_plaid_trial.py.
"""
import copy
import asyncio
import hashlib
import json
import logging
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
    public_state, apply_mapping_selection, apply_evidence_decision, merchant_rule_key, SelectionBody,
    EvidenceReviewBody, AccountProfilesBody, TransactionReviewBody, record_balances)
import bonus_plaid_trial as quota
from finance_history_archive import archive_old, read_month, archive_key, purge_account
from finance_account_management import (AccountAction, syncing, public_accounts, merge_profiles,
    action as account_action, purge_history, filter_after_removal)
from finance_ai_categories import classify_unknown, cache_key
from finance_plaid_enrichment import enrich as plaid_enrich

BACKGROUND_LOGGER = logging.getLogger(__name__)

DAY = 86400
MAX_BYTES = 8 * 1024 * 1024
MAX_PAGES = 25
MAX_ROWS = 10000
router = APIRouter(prefix="/api/bonus-tracker/plaid")

HISTORY_STATUS = frozenset(("NOT_READY", "INITIAL_UPDATE_COMPLETE", "HISTORICAL_UPDATE_COMPLETE"))
HISTORY_RETRY_HOURS = (1, 3, 6)


class SyncDelta(tuple):
    """A four-element cursor delta with optional Plaid historical-readiness metadata."""
    def __new__(cls, items, update_status=None, malformed=None):
        instance = super().__new__(cls, items)
        instance.update_status = update_status
        instance.malformed = malformed or {}
        return instance


def _retry_at(now, attempts):
    return now + (HISTORY_RETRY_HOURS[attempts - 1] * 3600 if 0 < attempts <= 3 else DAY)


def _sync_failure(state, now, detail):
    attempts = min(int(state.get("transactionRetryAttempts", 0)) + 1, 4)
    state["transactionRetryAttempts"] = attempts
    state["lastTransactionAttemptAt"] = now
    state["nextTransactionSyncAt"] = _retry_at(now, attempts)
    state["transactionSyncStatus"] = "needs_attention"
    state["transactionSyncError"] = detail



def connection_handle(uid, item_id):
    return hashlib.sha256((uid + "\0" + item_id).encode()).hexdigest()


ROUTES = frozenset(("/item/get", "/accounts/get", "/transactions/sync"))
_ITEM_PRODUCTS = {}  # Process-local verified item capabilities; never persist tokens.

def _catalog(token):
    """No product activation: /item/get is metadata-only."""
    hashed = hashlib.sha256(token.encode()).hexdigest()
    old = _ITEM_PRODUCTS.get(hashed)
    if old and old["expires"] > time.time():
        return old["catalog"]
    raw = _provider("/item/get", token)
    item = raw.get("item", {})
    if not isinstance(item, dict):
        raise HTTPException(502, "Plaid Item product availability could not be verified.")
    result = {}
    for field in ("products", "billed_products", "available_products"):
        values = item.get(field)
        if not isinstance(values, list) or any(not isinstance(value,str) for value in values):
            raise HTTPException(502, "Plaid Item product availability could not be verified.")
        result[field] = sorted(set(values))
    _ITEM_PRODUCTS[hashed] = {"expires": time.time()+300, "catalog":result}
    if len(_ITEM_PRODUCTS) > 100:
        _ITEM_PRODUCTS.clear()
    return result



def _private(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _key(uid):
    return "bonus-tracker-plaid-trial/v1/" + uid + "/activity.enc"


def _default():
    return {"version": 1, "items": {}, "monitored": [], "lastSuccess": None, "nextSyncAt": 0, "mappings": {},
            "newItemDiscoveryAttempts": {},
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
    # Move older provider data only after encrypted, conditional archive readback.
    # Transactions are Item-indexed here; archive shards use opaque account keys.
    source = {}
    for item_id, item in doc.get("items", {}).items():
        for ident, row in item.get("transactions", {}).items():
            if isinstance(row, dict):
                account = _account_key(item_id, str(row.get("account", "")))
                source.setdefault(account, {})[ident] = row
    hot, months = archive_old(store(), cipher(), "plaid", uid, source, int(time.time()))
    if months:
        doc["historyMonths"] = sorted(set(doc.get("historyMonths", [])) | set(months))
        for item_id, item in doc.get("items", {}).items():
            item["transactions"] = {
                ident: row for ident, row in item.get("transactions", {}).items()
                if ident in hot.get(_account_key(item_id, str(row.get("account", ""))), {})
            }
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
    if endpoint != "/item/get":
        catalog = _catalog(token)
        if endpoint == "/transactions/sync" and "transactions" not in (
                set(catalog["products"]) | set(catalog["billed_products"])):
            raise HTTPException(403, "This Item does not have an existing Transactions product. No paid activation attempted.")
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
            "mask": (str(row.get("mask")) if isinstance(row.get("mask"), str) and len(row["mask"]) == 4 and row["mask"].isdigit() else None),
            "bank": clean(institution, 100),
            "currency": clean(balance.get("iso_currency_code") or
                               balance.get("unofficial_currency_code"), 8),
            "balance": _money(balance.get("current")),
            "availableBalance": _money(balance.get("available")),
            "creditLimit": _money(balance.get("limit")),
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
            "observedAt": now, "lastObservedAt": now, **plaid_enrich(row)}


def _sync_item(token, current_cursor):
    """Apply all pages atomically, never persist a partial cursor."""
    cursor = current_cursor or ""
    added, modified, removed = {}, {}, set()
    malformed = {}
    update_status = None
    for _ in range(MAX_PAGES):
        response = _provider("/transactions/sync", token, cursor=cursor, count=500)
        if not isinstance(response, dict):
            raise HTTPException(502, "Plaid returned invalid transaction updates.")
        new_cursor, more = response.get("next_cursor"), response.get("has_more")
        observed = response.get("transactions_update_status")
        if observed in HISTORY_STATUS:
            update_status = observed
        if not isinstance(new_cursor, str) or type(more) is not bool:
            raise HTTPException(502, "Plaid sync pagination is invalid.")
        for kind, destination in (("added", added), ("modified", modified)):
            rows = response.get(kind)
            if not isinstance(rows, list) or len(rows) > 500:
                raise HTTPException(502, "Plaid transaction page is invalid.")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("transaction_id"), str) or not row["transaction_id"]:
                    account = row.get("account_id", "unknown") if isinstance(row, dict) else "unknown"
                    malformed[str(account)] = malformed.get(str(account), 0) + 1
                    continue
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
            return SyncDelta((added, modified, removed, cursor), update_status, malformed)
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
    previous = {a['key']: a.get('firstSeenAt',now) for a in out.get('accounts',[]) if isinstance(a,dict) and a.get('key')}
    for account in accounts:
        account['firstSeenAt'] = previous.get(account['key'],now)
    out["accounts"] = accounts
    # Plaid only certifies when these values were observed by our adapter,
    # not when the financial institution actually produced them. Persist
    # additive snapshots with the same bounded compaction as SimpleFIN.
    record_balances(out, accounts, now)
    if changes is None:
        out.setdefault("transactions", {})
        out.setdefault("cursor", "")
        return out
    added, modified, removed, cursor = changes
    malformed = dict(getattr(changes, "malformed", {}) or {})
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
        try:
            converted = _transaction(row, now)
        except HTTPException:
            account = row.get("account_id", "unknown") if isinstance(row, dict) else "unknown"
            malformed[str(account)] = malformed.get(str(account), 0) + 1
            continue
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
    # Preserve all received history until verified encrypted monthly archival;
    # the provider's explicit remove/replacement delta controls pending rows.
    out["transactions"] = transactions
    out["cursor"] = cursor
    out["invalidTransactionsByAccount"] = {
        _account_key(item_id, raw): count for raw, count in malformed.items() if raw != "unknown"
    }
    out["unassignedInvalidTransactions"] = malformed.get("unknown", 0)
    out.setdefault("firstTransactionAttemptAt", now)
    out["lastTransactionAttemptAt"] = now
    out["lastTransactionSyncAt"] = now
    out.pop("transactionSyncError", None)
    update_status = getattr(changes, "update_status", None)
    if update_status in HISTORY_STATUS:
        out["transactionUpdateStatus"] = update_status
    if update_status in ("NOT_READY", "INITIAL_UPDATE_COMPLETE"):
        attempts = min(int(out.get("transactionRetryAttempts", 0)) + 1, 4)
        out["transactionRetryAttempts"] = attempts
        out["nextTransactionSyncAt"] = _retry_at(now, attempts)
        out["transactionSyncStatus"] = "history_still_loading"
    else:
        out["transactionRetryAttempts"] = 0 if out.get("transactionUpdateStatus") == "HISTORICAL_UPDATE_COMPLETE" else out.get("transactionRetryAttempts", 0)
        out["nextTransactionSyncAt"] = now + DAY
        out["transactionSyncStatus"] = (
            "ready" if out.get("transactionUpdateStatus") == "HISTORICAL_UPDATE_COMPLETE"
            else "ready" if any(not x.get("pending") for x in out["transactions"].values())
            else "pending_only" if out["transactions"] else "awaiting_provider_data")
        if out.get("transactionUpdateStatus") in ("NOT_READY", "INITIAL_UPDATE_COMPLETE"):
            out["transactionSyncStatus"] = "history_still_loading"
    return out


def _view(doc, ledger, uid):
    accessible = {item_id for item_id, row in ledger["items"].items()
                  if isinstance(row, dict) and row.get("owner") == uid and
                  ((not row.get("disconnectedAt") and not row.get("disconnectingAt")) or row.get("retainHistory"))}
    view = {"access": bool(accessible), "accounts": [], "transactions": {},
            "balanceHistory": {},
            "financeAICache": doc.get("financeAICache", {}),
            "financeAILastSync": doc.get("financeAILastSync", {}),
            "merchantRules": doc.get("merchantRules", {}),
            "accountExclusions": doc.get("accountExclusions", {}),
            "health": doc.get("health", {}),
            "monitoredAccounts": list(doc.get("monitored", [])),
            **{key: copy.deepcopy(doc.get(key, {})) for key in
               ("mappings", "evidenceReviews", "accountProfiles", "transactionReviews")},
            "lastSuccess": doc.get("lastSuccess"), "nextSyncAt": doc.get("nextSyncAt"),
            "historyMonths": doc.get("historyMonths", []),
            "error": doc.get("error"), "status": "attention" if doc.get("error") else "connected"}
    permitted = set(doc.get("monitored", []))
    for item_id, state in doc.get("items", {}).items():
        if item_id not in accessible:
            continue
        for account in public_accounts(doc, state.get("accounts", [])):
            key = account["key"]
            history = state.get("balanceHistory", {}).get(key, [])
            if isinstance(history, list):
                view["balanceHistory"][key] = copy.deepcopy(history)
            view["accounts"].append({**{k: v for k, v in account.items() if k != "id"},
                                     "connectionHandle": connection_handle(uid, item_id),
                                     "transactionSyncStatus": state.get("transactionSyncStatus", "awaiting_first_sync"),
                                     "transactionUpdateStatus": state.get("transactionUpdateStatus"),
                                     "invalidTransactionRows": state.get("invalidTransactionsByAccount", {}).get(key, 0),
                                     "historyComplete": (state.get("transactionUpdateStatus") == "HISTORICAL_UPDATE_COMPLETE"),
                                     "transactionFirstSyncAt": state.get("firstTransactionAttemptAt"),
                                     "transactionLastSyncAt": state.get("lastTransactionSyncAt"),
                                     "transactionNextSyncAt": state.get("nextTransactionSyncAt", doc.get("nextSyncAt")),
                                     "transactionSyncError": state.get("transactionSyncError"),
                                     "connectionStatus": "disconnected" if ledger["items"][item_id].get("disconnectedAt") else "disconnecting" if ledger["items"][item_id].get("disconnectingAt") else "connected"})
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
    filter_after_removal(doc, view["transactions"])
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
    new_profiles = {key: value.model_dump(exclude_none=True) for key, value in body.profiles.items()}
    resumed = {key for key, profile in new_profiles.items()
               if profile.get('syncEnabled') is True and
               doc.get('accountProfiles', {}).get(key, {}).get('syncEnabled') is False}
    _resume_accounts(doc, resumed)
    doc["accountProfiles"] = merge_profiles(doc.get("accountProfiles", {}), new_profiles)
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
        if body.applySimilar:
            doc.setdefault("merchantRules", {})[merchant_rule_key(body.account, rows[raw].get("description", ""))] = {
                "category": body.category, "updatedAt": int(time.time())}
    _write(uid, doc, etag)
    return _public(doc, ledger, uid)


@router.get("/accounts/capabilities", dependencies=[Depends(_private)])
def capabilities(request: Request):
    """Authenticated Item product inventory. Never triggers optional product calls."""
    uid = owner(request)
    ledger, _tag = quota._ledger()
    active = {item_id:item for item_id,item in ledger.get("items",{}).items()
              if isinstance(item,dict) and item.get("owner")==uid and
              not item.get("disconnectedAt") and not item.get("disconnectingAt")}
    result = []
    for item_id,item in active.items():
        try:
            catalog = _catalog(item["access"])
            result.append({"handle":connection_handle(uid,item_id),
                           "institution":clean(item.get("institution"),100),
                           **catalog,
                           "enabledData":["balances","transactions"] if "transactions" in catalog["products"] else ["balances"],
                           "optionalProductsEnabled":[],
                           "extraCostProductsCalled":False})
        except (KeyError,HTTPException):
            result.append({"handle":connection_handle(uid,item_id),
                           "institution":clean(item.get("institution"),100),
                           "error":"Product inventory unavailable. Optional products remain disabled.",
                           "optionalProductsEnabled":[],"extraCostProductsCalled":False})
    return {"items":result,"noNewItems":True,"paidAddonsEnabled":False}


@router.get("/accounts/history/{month}", dependencies=[Depends(_private)])
def history_month(month: str, request: Request):
    uid = owner(request)
    try:
        archive_key("plaid", uid, month)
    except ValueError:
        raise HTTPException(400, "Choose a valid month.")
    ledger, _ = quota._ledger()
    allowed_items = {ident for ident, row in ledger.get("items", {}).items()
                     if row.get("owner") == uid and not row.get("disconnectedAt") and not row.get("disconnectingAt")}
    doc, _ = _read(uid)
    account_keys = {a["key"] for item, rows in doc.get("items", {}).items()
                    if item in allowed_items for a in rows.get("accounts", [])}
    archived = read_month(store(), cipher(), "plaid", uid, month)
    return {"month": month, "transactions": {
        key: {ident: {k: v for k, v in tx.items() if k not in ("id", "account")}
              for ident, tx in rows.items()}
        for key, rows in archived.items() if key in account_keys}}


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
def disconnect_account_item(handle: str, request: Request, delete_history: bool = False):
    return quota.disconnect_item(handle, request, delete_history=delete_history)


@router.post("/accounts/sync", dependencies=[Depends(_private)])
def sync(request: Request):
    return sync_owner(owner(request))


def sync_owner(uid):
    ledger, _ = quota._ledger()
    known = {item_id: row for item_id, row in ledger["items"].items()
             if row.get("owner") == uid and not row.get("disconnectedAt") and not row.get("disconnectingAt")}
    if not known:
        raise HTTPException(409, "Connect a Plaid fallback institution first.")
    doc, etag = _read(uid)
    now = int(time.time())
    known = {ident: item for ident, item in known.items()
             if not doc.get('items', {}).get(ident, {}).get('accounts') or
             any(syncing(doc, a) for a in doc['items'][ident]['accounts'])}
    if not known:
        return _public(doc, ledger, uid)
    if doc.get("leaseUntil", 0) > now:
        raise HTTPException(409, "Plaid synchronization is busy.")
    if now < doc.get("nextSyncAt", 0):
        # The owner-wide daily budget protects *existing* Items. A just-linked
        # Item may still make its FIRST transaction request without re-querying
        # previously synced banks. This prevents a new OnePay bank showing an
        # account balance but no activity until an unrelated bank's next window.
        missing = {item_id: item for item_id, item in known.items()
                   if item_id not in doc.get("items", {})}
        attempts = doc.setdefault("newItemDiscoveryAttempts", {})
        eligible_missing = {item_id: item for item_id, item in missing.items()
                            if now >= attempts.get(item_id, 0)}
        # Independent per-Item budget also handles delayed first-history
        # backfill, not only the initial request. Legacy Items with a cursor
        # and no Item-specific deadline still use the owner-wide daily window.
        due_items = {item_id: item for item_id, item in known.items()
                     if item_id in doc.get("items", {}) and
                     doc["items"][item_id].get("accounts") and
                     (("nextTransactionSyncAt" in doc["items"][item_id] and
                       now >= doc["items"][item_id]["nextTransactionSyncAt"]) or
                      (not doc["items"][item_id].get("lastTransactionAttemptAt") and
                       not doc["items"][item_id].get("cursor")))}
        if not eligible_missing and not due_items:
            return _public(doc, ledger, uid)
        doc["lease"] = uuid.uuid4().hex
        doc["leaseUntil"] = now + 300
        if eligible_missing:
            doc["nextNewItemDiscoveryAt"] = now + DAY  # legacy visibility
            for item_id in eligible_missing:
                attempts[item_id] = now + DAY
        etag = _write(uid, doc, etag)
        candidate = copy.deepcopy(doc)
        try:
            discovery_failed = False
            history_failed = False
            for item_id, item in eligible_missing.items():
                try:
                    raw = _provider("/accounts/get", item["access"])
                    state = _merge({}, item_id, item["institution"], raw, None, now)
                except HTTPException:
                    # Keep the failed Item eligible for a later bounded retry
                    # without rolling back other newly linked institutions.
                    discovery_failed = True
                    continue
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
                        _sync_failure(state, now, exc.detail)
                        history_failed = True
                candidate["items"][item_id] = state
                candidate.get("newItemDiscoveryAttempts", {}).pop(item_id, None)
            for item_id, item in due_items.items():
                old = candidate["items"][item_id]
                try:
                    delta = _sync_item(item["access"], old.get("cursor"))
                    # Refresh real bank balances alongside first-time history;
                    # never forge a new balance timestamp from cached metadata.
                    raw = _provider("/accounts/get", item["access"])
                    candidate["items"][item_id] = _merge(
                        old, item_id, item["institution"], raw, delta, now)
                    candidate["lastSuccess"] = now
                except HTTPException as exc:
                    _sync_failure(old, now, exc.detail)
                    history_failed = True
            if eligible_missing:
                candidate["lastAccountDiscovery"] = now
            if discovery_failed or history_failed:
                candidate["error"] = "One or more linked banks need an update. Retry after the next provider window."
            else:
                candidate.pop("error", None)
        except HTTPException as exc:
            # No partial encrypted writes or lost prior activity on failed
            # account discovery. Retry after the reserved provider interval.
            candidate = doc
            candidate["error"] = exc.detail
        _preserve_excluded(candidate, doc)
        candidate.pop("lease", None)
        candidate.pop("leaseUntil", None)
        try:
            classify_unknown(candidate, now=now)
        except Exception as error:
            BACKGROUND_LOGGER.warning("finance_category_error class=%s", type(error).__name__)
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
        refreshed = False
        failed = False
        for item_id, item in known.items():
            old = candidate["items"].get(item_id, {})
            try:
                raw = _provider("/accounts/get", item["access"])
                if old.get("accounts") and not raw.get("accounts"):
                    raise HTTPException(502, "This bank did not return its previously linked accounts.")
                exposed = _merge(old, item_id, item["institution"], raw, None, now)
            except HTTPException as exc:
                failed = True
                # A single bank's account-discovery outage must not destroy
                # saved transactions or prevent other Items from updating.
                if old:
                    exposed = copy.deepcopy(old)
                    _sync_failure(exposed, now, exc.detail)
                    candidate["items"][item_id] = exposed
                continue
            discovered = {a["key"] for a in exposed["accounts"]}
            refreshed = True
            new_accounts = discovered - monitored
            monitored.update(discovered)
            candidate["monitored"] = sorted(monitored)
            if new_accounts and old.get("cursor"):
                # A preexisting cursor may have skipped activity for newly linked accounts.
                old = {**old, "cursor": ""}
            # Respect a newly linked Item's own first-sync budget. A daily
            # owner refresh must not issue a second request for that Item.
            if discovered and now >= old.get("nextTransactionSyncAt", 0):
                try:
                    changes = _sync_item(item["access"], old.get("cursor"))
                    exposed = _merge(old, item_id, item["institution"], raw, changes, now)
                except HTTPException as exc:
                    failed = True
                    # A temporarily unavailable Item must not roll back
                    # successfully synced unrelated banks or lose its cursor.
                    _sync_failure(exposed, now, exc.detail)
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
        if refreshed:
            candidate["lastSuccess"] = now
        if failed:
            candidate["error"] = "Some linked banks need an update. Existing activity is preserved."
        else:
            candidate.pop("error", None)
    except HTTPException as exc:
        # Fail closed: no partial cursor or transaction import is acknowledged.
        candidate = doc
        candidate["error"] = exc.detail
    _preserve_excluded(candidate, doc)
    candidate.pop("lease", None)
    candidate.pop("leaseUntil", None)
    try:
        classify_unknown(candidate, now=now)
    except Exception as error:
        BACKGROUND_LOGGER.warning("finance_category_error class=%s", type(error).__name__)
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
            now = time.time()
            owned = {item_id for item_id, item in ledger["items"].items()
                     if item.get("owner") == uid and not item.get("disconnectedAt") and not item.get("disconnectingAt")}
            discover_due = any(item_id not in doc.get("items", {}) and
                now >= doc.get("newItemDiscoveryAttempts", {}).get(item_id, 0)
                for item_id in owned)
            item_due = any(
                item_id in owned and state.get("accounts") and
                (("nextTransactionSyncAt" in state and
                  now >= state["nextTransactionSyncAt"]) or
                 (not state.get("lastTransactionAttemptAt") and not state.get("cursor")))
                for item_id, state in doc.get("items", {}).items())
            if now >= doc.get("nextSyncAt", 0) or discover_due or item_due:
                sync_owner(uid)
        except Exception as error:
            # Only the exception class is logged; no account, token or bank data.
            BACKGROUND_LOGGER.warning("plaid_owner_sync_error class=%s", type(error).__name__)


async def scheduled_plaid_loop():
    while True:
        try:
            await asyncio.to_thread(scheduled_sync_once)
        except Exception as error:
            BACKGROUND_LOGGER.warning("plaid_scheduled_sync_error class=%s", type(error).__name__)
        await asyncio.sleep(300)


def _preserve_excluded(candidate, previous):
    for item_id, state in candidate.get('items', {}).items():
        old = previous.get('items', {}).get(item_id, {})
        state['transactions'] = {k: v for k, v in state.get('transactions', {}).items()
            if v.get('posted', 0) > candidate.get('accountExclusions', {}).get(
                _account_key(item_id, v.get('account', '')), {}).get('deletedBefore', 0)}
        disabled = {a.get('id') for a in state.get('accounts', []) if not syncing(candidate, a)}
        for account in old.get('accounts', []):
            if account['key'] in candidate.get('accountExclusions', {}) and not any(a['key'] == account['key'] for a in state.get('accounts', [])):
                state.setdefault('accounts', []).append(account)
                disabled.add(account.get('id'))
        if not disabled:
            continue
        state['transactions'] = {k: v for k, v in state.get('transactions', {}).items() if v.get('account') not in disabled}
        state['transactions'].update({k: v for k, v in old.get('transactions', {}).items() if v.get('account') in disabled})
        old_accounts = {a['key']: a for a in old.get('accounts', [])}
        state['accounts'] = [old_accounts.get(a['key'], a) if a.get('id') in disabled else a for a in state['accounts']]
    errors = [state for state in candidate.get('items', {}).values() if state.get('transactionSyncStatus') == 'needs_attention']
    candidate['health'] = {'lastSuccess': candidate.get('lastSuccess'),
        'lastError': 'HTTPException' if errors else None,
        'nextRetry': min((x.get('nextTransactionSyncAt', 0) for x in errors), default=None)}


@router.post('/accounts/account-action', dependencies=[Depends(_private)])
def manage_account(body: AccountAction, request: Request):
    uid, ledger, doc, etag, view = _editable(request)
    was_removed = bool(doc.get('accountExclusions', {}).get(body.account, {}).get('removedAt'))
    result = account_action(doc, view['accounts'], body)
    if body.action == 'restore' and was_removed:
        _resume_accounts(doc, {body.account})
    if result.get('delete'):
        purge_account(store(), cipher(), 'plaid', uid, doc.get('historyMonths', []), body.account)
        purge_history(doc, body.account)
    _write(uid, doc, etag)
    return {**_public(doc, ledger, uid), 'accountAction': result}


def _resume_accounts(doc, keys):
    """Resume an existing Item without losing rows skipped while an account was paused.

    Reset only its cursor; keep the already-reserved provider deadline and Trial ledger.
    """
    for state in doc.get('items', {}).values():
        if any(a['key'] in keys for a in state.get('accounts', [])):
            state['cursor'] = ''
            state['transactionSyncStatus'] = 'history_still_loading'
            state.pop('transactionUpdateStatus', None)

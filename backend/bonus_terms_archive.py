"""Owner-scoped, immutable historical BonusTracker offer terms in Cloudflare R2.

These snapshots are independent of rotating recovery backups. They are evidence
archives, not a source for overwriting an enrolled offer or changing rule engines.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from botocore.exceptions import ClientError
from starlette.concurrency import run_in_threadpool

from bonus_backups import owner, store

router = APIRouter()
MAX_TERMS_BYTES = 512 * 1024
PREFIX = "bonus-tracker-terms/v1/"
SHA = re.compile(r"^[0-9a-f]{64}$")
# Explicitly exclude user accounts, bank balances, authentication and transactions.
FIELDS = frozenset((
    "format", "version", "bank", "accountType", "entryId", "opened",
    "bonus", "capturedAt", "termsVerifiedAt", "source", "schemaVersion",
    "promoSourceUrl", "feeScheduleSourceUrl", "tcSourceRaw", "feeScheduleRaw",
    "financialRules", "eligibilityRules", "eligibilityEvidenceText",
    "eligibilityEvidenceSource", "currentCustomerEvidenceText",
    "completeBonusText", "monthlyFeeYNText", "monthlyFeeAmountText",
    "monthlyFeeWaiverText", "avoidMonthlyFeeText", "earlyTerminationFeeText",
    "closeRuleText", "closeRuleBasis", "minHoldDays", "earlyCloseFee",
    "expirationDateText", "payoutTimingText", "requiredDaysText",
    "eligibilityText", "sourceEligibilityBasis", "churnPeriodValue",
    "churnPeriodUnit", "couponEnrollmentDate", "offerEnrollmentDate",
    "closeRestrictionType", "closeBufferDays", "closeFeeCountdownDays",
    "closedWithNegativeBalance", "eligibilityScope", "eligibilityAnchorEvidenceText",
    "reapplicationAction", "monthlyFeeFrequency", "monthlyFeeWaiverType",
    "monthlyFeeWaiverAmountText", "monthlyFeeNoMonthlyServiceFee", "tcSourceId",
    "tcSourceUpdatedAt",
))
STRING_FIELDS = FIELDS - frozenset((
    "format", "version", "bonus", "schemaVersion", "financialRules",
    "eligibilityRules", "minHoldDays", "earlyCloseFee", "churnPeriodValue",
    "closeBufferDays", "closeFeeCountdownDays", "closedWithNegativeBalance",
    "monthlyFeeNoMonthlyServiceFee",
))
SENSITIVE_FIELD = re.compile(
    r"(?:password|credential|(?:api|auth|access|refresh)[_-]?token|secret|"
    r"routing[_-]?number|account[_-]?number|private[_-]?key|email|"
    r"transaction[s]?|bank[_-]?balance)", re.I
)


def _safe_structure(value, depth=0):
    if depth > 16:
        raise ValueError("Terms rule structure is too deep")
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError("Terms rule object is too large")
        for key, item in value.items():
            if not isinstance(key, str) or SENSITIVE_FIELD.search(key):
                raise ValueError("Unsupported private field in terms")
            _safe_structure(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 500:
            raise ValueError("Terms rule list is too large")
        for item in value:
            _safe_structure(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("Unsupported terms data type")


def validate_terms(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode("utf-8")) <= MAX_TERMS_BYTES:
        raise ValueError("Historical terms snapshot is too large or empty")
    data = json.loads(raw)
    if not isinstance(data, dict) or not set(data).issubset(FIELDS):
        raise ValueError("Unrecognized historical terms fields")
    if data.get("format") != "BonusTrackerHistoricalTerms" or data.get("version") != 1:
        raise ValueError("Unsupported historical terms format")
    if not isinstance(data.get("bank"), str) or not data["bank"].strip() or len(data["bank"]) > 160:
        raise ValueError("Invalid bank identity")
    if not isinstance(data.get("entryId"), str) or not 1 <= len(data["entryId"]) <= 160:
        raise ValueError("Historical offer identity is required")
    if data.get("accountType") not in ("personal", "business"):
        raise ValueError("Unknown account audience")
    for key in STRING_FIELDS:
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 350000):
            raise ValueError("Invalid terms text")
    for key in ("bonus", "schemaVersion", "minHoldDays", "earlyCloseFee",
                "churnPeriodValue", "closeBufferDays", "closeFeeCountdownDays"):
        if key in data and (isinstance(data[key], bool) or not isinstance(data[key], (int, float))
                           or not -1_000_000 <= data[key] <= 1_000_000):
            raise ValueError("Invalid historical term number")
    for key in ("closedWithNegativeBalance", "monthlyFeeNoMonthlyServiceFee"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError("Invalid historical terms boolean")
    if "financialRules" in data and data["financialRules"] is not None:
        if not isinstance(data["financialRules"], dict):
            raise ValueError("Financial rules must be an object")
        _safe_structure(data["financialRules"])
    if "eligibilityRules" in data:
        if not isinstance(data["eligibilityRules"], list):
            raise ValueError("Eligibility rules must be a list")
        _safe_structure(data["eligibilityRules"])
    if not any(str(data.get(x) or "").strip() for x in (
        "tcSourceRaw", "feeScheduleRaw", "earlyTerminationFeeText",
        "monthlyFeeYNText", "monthlyFeeAmountText", "closeRuleText",
        "completeBonusText",
    )) and not data.get("financialRules"):
        raise ValueError("No historical terms evidence was supplied")
    return data


def _key(uid, digest):
    if not SHA.fullmatch(digest):
        raise HTTPException(400, "Invalid historical terms identity.")
    return f"{PREFIX}{uid}/{digest}.json"


def read_terms(storage, uid, digest):
    key = _key(uid, digest)
    try:
        head = storage.client.head_object(Bucket=storage.bucket, Key=key)
        raw, _ = storage.read(key)
    except FileNotFoundError:
        raise HTTPException(404, "Historical terms snapshot was not found.")
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
            raise HTTPException(404, "Historical terms snapshot was not found.")
        raise
    if (len(raw) > MAX_TERMS_BYTES or
            hashlib.sha256(raw).hexdigest() != digest or
            head.get("Metadata", {}).get("sha256") != digest):
        raise ValueError("Historical terms integrity verification failed")
    snapshot = raw.decode("utf-8")
    data = validate_terms(snapshot)
    return {
        "id": digest, "sha256": digest, "verified": True,
        "createdAt": head.get("Metadata", {}).get("created-at"),
        "bank": data["bank"], "accountType": data["accountType"],
        "entryId": data["entryId"], "opened": data.get("opened", ""),
        "snapshot": snapshot,
    }


def save_terms(storage, uid, packet):
    if not isinstance(packet, dict) or set(packet) != {"snapshot", "sha256"}:
        raise ValueError("Unexpected historical terms upload")
    raw_text = packet["snapshot"]
    data = validate_terms(raw_text)
    raw = raw_text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != packet["sha256"]:
        raise ValueError("Historical terms checksum mismatch")
    key = _key(uid, digest)
    metadata = {
        "sha256": digest, "created-at": datetime.now(timezone.utc).isoformat(),
        # R2 object metadata must remain ASCII; names/text stay in the signed body.
    }
    try:
        storage.client.put_object(
            Bucket=storage.bucket, Key=key, Body=raw,
            ContentType="application/json", Metadata=metadata, IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
    # Idempotent and immutable, even under concurrent uploads from multiple devices.
    result = read_terms(storage, uid, digest)
    return {k: result[k] for k in ("id", "sha256", "verified", "createdAt", "bank", "entryId")}


def list_terms(storage, uid, offset=0):
    prefix = f"{PREFIX}{uid}/"
    keys = []
    continuation = None
    while True:
        args = {"Bucket": storage.bucket, "Prefix": prefix, "MaxKeys": 1000}
        if continuation:
            args["ContinuationToken"] = continuation
        response = storage.client.list_objects_v2(**args)
        keys.extend((x["Key"], x["LastModified"]) for x in response.get("Contents", [])
                    if x["Key"].startswith(prefix) and x["Key"].endswith(".json"))
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
        if not continuation:
            raise ValueError("Incomplete historical terms catalog")
    keys.sort(key=lambda item: (item[1], item[0]), reverse=True)
    out = []
    for key, last_modified in keys[offset:offset + 50]:
        digest = key[len(prefix):-5]
        if not SHA.fullmatch(digest):
            continue
        item = read_terms(storage, uid, digest)
        out.append({k: item[k] for k in ("id", "sha256", "verified", "bank",
                                         "accountType", "entryId", "opened", "createdAt")})
    return {"terms": out, "total": len(keys),
            "nextOffset": offset + 50 if offset + 50 < len(keys) else None,
            "provider": "Cloudflare R2", "immutable": True}


async def _operation(fn, *args):
    try:
        return await run_in_threadpool(fn, *args)
    except HTTPException:
        raise
    except (ValueError, UnicodeError, json.JSONDecodeError):
        raise HTTPException(422, "Historical terms failed validation or integrity verification.")
    except Exception:
        raise HTTPException(503, "Historical terms storage is temporarily unavailable.")


@router.post("/api/bonus-tracker/terms")
async def put_historical_terms(request: Request):
    uid = owner(request)
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_TERMS_BYTES * 2 + 4096:
            raise HTTPException(413, "Historical terms upload too large.")
    try:
        packet = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid historical terms upload.")
    return JSONResponse(await _operation(save_terms, store(), uid, packet),
                        headers={"Cache-Control": "no-store"})


@router.get("/api/bonus-tracker/terms")
async def get_historical_terms(request: Request, offset: int = Query(0, ge=0, le=100000)):
    uid = owner(request)
    return JSONResponse(await _operation(list_terms, store(), uid, offset),
                        headers={"Cache-Control": "no-store"})


@router.get("/api/bonus-tracker/terms/{digest}")
async def get_historical_terms_record(digest: str, request: Request):
    uid = owner(request)
    _key(uid, digest)
    return JSONResponse(await _operation(read_terms, store(), uid, digest),
                        headers={"Cache-Control": "no-store"})

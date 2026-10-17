"""Owner-run, authenticated, dry-run-first cleanup of retired Finance R2 records.

Never include the Plaid global slot ledger, bonuses, vehicle data or journal records.
The route has no provider integrations. It does not run automatically.
"""
import hashlib
import re
from collections import Counter

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bonus_backups import owner, store

router = APIRouter(prefix="/api/bonus-tracker/finance-retirement", tags=["Finance retirement"])
PHRASE = "DELETE FINANCE DATA"
MAX_OBJECTS = 10000
MONTH = r"[0-9]{4}-(?:0[1-9]|1[0-2])\.enc"


def _rules(uid):
    """All scans are scoped to the authenticated owner's opaque identifier."""
    if not re.fullmatch(r"[a-f0-9]{64}", uid):
        raise HTTPException(403, "Invalid cleanup scope.")
    sf = "bonus-tracker-simplefin/v1/" + uid + "/"
    plaid = "bonus-tracker-plaid/v1/" + uid + "/"
    trial = "bonus-tracker-plaid-trial/v1/" + uid + "/"
    prefs = "bonus-tracker-finances/v1/" + uid + "/"
    return (
        (sf, (
            ("simplefin_state", re.compile(re.escape(sf) + r"state\.enc\Z")),
            ("simplefin_history", re.compile(re.escape(sf) + r"history/" + MONTH + r"\Z")),
            # Preserve the explicitly identified old pre-disconnect location only.
            # Unknown legacy locations must be reported rather than wildcard-purged.
            ("simplefin_pre_disconnect", re.compile(re.escape(sf) + r"pre-disconnect/[A-Za-z0-9_-]{1,80}\.enc\Z")),
        )),
        (plaid, (("plaid_history", re.compile(re.escape(plaid) + r"history/" + MONTH + r"\Z")),)),
        (trial, (("plaid_activity", re.compile(re.escape(trial) + r"activity\.enc\Z")),)),
        (prefs, (("finance_preferences", re.compile(re.escape(prefs) + r"preferences\.enc\Z")),)),
    )


CATEGORIES = (
    "simplefin_state", "simplefin_history", "simplefin_pre_disconnect",
    "plaid_history", "plaid_activity", "finance_preferences",
)


def _scan(storage, uid):
    """Only counts/prefix identifiers escape this function; raw keys stay private."""
    matched = []
    blocked = Counter()
    for prefix, rules in _rules(uid):
        cursor = None
        while True:
            kwargs = {"Bucket": storage.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if cursor:
                kwargs["ContinuationToken"] = cursor
            page = storage.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents", ()):
                key = obj.get("Key")
                if not isinstance(key, str) or not key.startswith(prefix):
                    raise HTTPException(503, "Unsafe R2 listing result.")
                category = next((name for name, pattern in rules if pattern.fullmatch(key)), None)
                if category is None:
                    blocked[prefix.split("/")[0]] += 1
                else:
                    matched.append((category, key, str(obj.get("ETag", ""))))
                if len(matched) + sum(blocked.values()) > MAX_OBJECTS:
                    raise HTTPException(413, "Cleanup requires a smaller audited scope.")
            if not page.get("IsTruncated", False):
                break
            next_cursor = page.get("NextContinuationToken")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                raise HTTPException(503, "Incomplete R2 listing.")
            cursor = next_cursor
    return matched, blocked


def _fingerprint(matched):
    h = hashlib.sha256()
    for category, key, etag in sorted(matched):
        h.update((category + "\0" + key + "\0" + etag + "\n").encode())
    return h.hexdigest()


def _plan(storage, uid):
    matched, blocked = _scan(storage, uid)
    counts = Counter(row[0] for row in matched)
    return {
        "dryRun": True,
        "counts": {category: counts[category] for category in CATEGORIES},
        "total": len(matched),
        "blocked": sum(blocked.values()),
        "digest": _fingerprint(matched),
    }, matched


@router.get("/dry-run")
def dry_run(request: Request):
    """Owner must inspect counts and retain the returned digest before execution."""
    uid = owner(request)
    result, _ = _plan(store(), uid)
    return result


class Confirmation(BaseModel):
    phrase: str = Field(max_length=40)
    count: int = Field(ge=0, le=MAX_OBJECTS)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/execute")
def execute(body: Confirmation, request: Request):
    """Owner-triggered only; no background cleanup, no provider requests."""
    uid = owner(request)
    if body.phrase != PHRASE:
        raise HTTPException(400, "Typed confirmation phrase does not match.")
    storage = store()
    current, matched = _plan(storage, uid)
    if current["blocked"] or body.count != current["total"] or body.digest != current["digest"]:
        raise HTTPException(409, "R2 cleanup scope changed or contains unreviewed keys. Repeat dry run.")
    # Delete only keys positively matched to the authenticated owner and reviewed.
    for _category, key, etag in matched:
        # Fail closed on stale objects; never delete a key that changed since scanning.
        actual = storage.client.head_object(Bucket=storage.bucket, Key=key)
        if str(actual.get("ETag", "")) != etag:
            raise HTTPException(409, "R2 data changed; repeat dry run.")
        storage.client.delete_object(Bucket=storage.bucket, Key=key)
    verified, _ = _plan(storage, uid)
    if verified["total"]:
        raise HTTPException(503, "R2 cleanup could not verify completion.")
    # Do not log owner IDs, object keys, bank names, or contents.
    return {"deleted": len(matched), "remaining": 0, "blocked": verified["blocked"], "verified": True}

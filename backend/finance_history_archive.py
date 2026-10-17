"""Owner-scoped encrypted history shards; append-only preservation and verified writes.

All provider content is retained verbatim. Moving a record out of the hot state
is allowed only after a successful conditional archive write AND readback.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from fastapi import HTTPException

DAY = 86400
HOT_DAYS = 90
HISTORY_DAYS = 731
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
PROVIDERS = frozenset(("simplefin", "plaid"))


def _month(ts):
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m")
    except (ValueError, TypeError, OSError, OverflowError):
        return None


def archive_key(provider, owner_id, month):
    if provider not in PROVIDERS or not isinstance(owner_id, str) or not owner_id:
        raise ValueError("Invalid private archive scope")
    if len(month) != 7 or month[4] != "-" or not month[:4].isdigit() or not month[5:].isdigit() or not 1 <= int(month[5:]) <= 12:
        raise ValueError("Invalid archive month")
    return "bonus-tracker-" + provider + "/v1/" + owner_id + "/history/" + month + ".enc"


def _read_bucket(s, cipher, key):
    try:
        response = s.client.get_object(Bucket=s.bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return {}, None
        raise HTTPException(503, "History storage is temporarily unavailable.") from None
    try:
        raw = response["Body"].read(MAX_ARCHIVE_BYTES + 1)
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("oversized")
        doc = json.loads(cipher.decrypt(raw))
        if not isinstance(doc, dict) or not isinstance(doc.get("accounts", {}), dict):
            raise ValueError("invalid")
        return doc["accounts"], response["ETag"]
    except (ValueError, TypeError, KeyError, Exception) as exc:
        # Avoid returning provider text or encrypted secrets to the caller.
        raise HTTPException(503, "History integrity check failed. Original records were not changed.") from None


def read_month(s, cipher, provider, owner_id, month):
    return copy.deepcopy(_read_bucket(s, cipher, archive_key(provider, owner_id, month))[0])


def _write_bucket(s, cipher, key, accounts, etag):
    raw = cipher.encrypt(json.dumps({"version": 1, "accounts": accounts},
                                    sort_keys=True, separators=(",", ":")).encode())
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise HTTPException(413, "History archive is full; the original records were preserved.")
    args = {"Bucket": s.bucket, "Key": key, "Body": raw,
            "ContentType": "application/octet-stream",
            **({"IfMatch": etag} if etag else {"IfNoneMatch": "*"})}
    try:
        s.client.put_object(**args)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "ConditionalRequestConflict", "409", "412"):
            raise HTTPException(409, "History archive was updated concurrently; retry safely.") from None
        raise HTTPException(503, "History archive could not be saved.") from None
    saved, _etag = _read_bucket(s, cipher, key)
    if saved != accounts:
        raise HTTPException(503, "History readback failed. Original records were preserved.")


def archive_old(s, cipher, provider, owner_id, transactions, now):
    """Return hot rows and archived months without mutating the input.

    The returned hot map is safe to save only after all archive writes/readbacks.
    Archives are append-only at record level: a provider correction is retained
    as an extra revision if its value differs (never silently overwritten).
    """
    cutoff = now - HOT_DAYS * DAY
    hot, staged, months = {}, {}, set()
    for account, rows in transactions.items():
        kept = {}
        if not isinstance(rows, dict):
            continue
        for ident, row in rows.items():
            if not isinstance(row, dict):
                kept[ident] = row
                continue
            timestamp = row.get("posted")
            month = _month(timestamp)
            if not month or not isinstance(timestamp, (float, int)) or timestamp >= cutoff:
                kept[ident] = row
                continue
            staged.setdefault(month, {}).setdefault(account, {})[ident] = copy.deepcopy(row)
            months.add(month)
        hot[account] = kept
    for month in sorted(staged):
        key = archive_key(provider, owner_id, month)
        stored, etag = _read_bucket(s, cipher, key)
        combined = copy.deepcopy(stored)
        for account, rows in staged[month].items():
            dest = combined.setdefault(account, {})
            for ident, row in rows.items():
                prior = dest.get(ident)
                if prior is not None and prior != row:
                    revisions = list(prior.get("archiveRevisions", [])) if isinstance(prior, dict) else []
                    revisions.append(prior)
                    row["archiveRevisions"] = revisions[-5:]
                dest[ident] = row
        _write_bucket(s, cipher, key, combined, etag)
    return hot, sorted(months)

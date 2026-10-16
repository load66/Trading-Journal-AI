"""Private, encrypted, immutable transaction shards referenced by CAS-protected state.

No provider credentials, unencrypted transaction payloads, or owner identifiers
are returned to clients. An interrupted write leaves the previous manifest valid.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from botocore.exceptions import ClientError
from cryptography.fernet import InvalidToken
from fastapi import HTTPException

ROWS_PER_SHARD = 200
MAX_SHARDS = 2048


def _payloads(records):
    buckets = {}
    for account, rows in sorted(records.items()):
        if not isinstance(rows, dict):
            raise HTTPException(503, "Financial transaction history needs review.")
        for identifier, row in sorted(rows.items()):
            if not isinstance(row, dict):
                raise HTTPException(503, "Financial transaction history needs review.")
            at = next((row.get(field) for field in
                       ("posted", "transactedAt", "displayAt", "observedAt")
                       if isinstance(row.get(field), (int, float)) and row[field] > 0), 0)
            try:
                month = datetime.fromtimestamp(at, tz=timezone.utc).strftime("%Y-%m") if at else "unknown"
            except (OverflowError, OSError, ValueError):
                month = "unknown"
            buckets.setdefault((account, month), []).append((identifier, row))
    for (account, month), rows in sorted(buckets.items()):
        for start in range(0, len(rows), ROWS_PER_SHARD):
            body = json.dumps({"account": account, "month": month,
                               "rows": dict(rows[start:start + ROWS_PER_SHARD])},
                              sort_keys=True, separators=(",", ":")).encode()
            yield body


def _read_object(client, bucket, object_key, max_bytes):
    try:
        response = client.get_object(Bucket=bucket, Key=object_key)
        raw = response["Body"].read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError()
        return raw
    except (ClientError, ValueError):
        raise HTTPException(503, "Private financial history is incomplete; no data was changed.") from None


def store_shards(client, bucket, prefix, records, old_manifest, fernet, max_bytes):
    """Stage new immutable shards, then let the caller publish the manifest via CAS."""
    old = {entry["sha256"]: entry for entry in (old_manifest or [])
           if isinstance(entry, dict) and isinstance(entry.get("sha256"), str)}
    manifest = []
    for body in _payloads(records):
        digest = hashlib.sha256(body).hexdigest()
        if digest in old:
            manifest.append(old[digest])
            continue
        object_key = prefix + "/history/" + uuid.uuid4().hex + ".enc"
        encrypted = fernet.encrypt(body)
        if len(encrypted) > max_bytes:
            raise HTTPException(413, "Financial history shard exceeded its safety limit.")
        try:
            client.put_object(Bucket=bucket, Key=object_key, Body=encrypted,
                              ContentType="application/octet-stream", IfNoneMatch="*")
        except ClientError:
            raise HTTPException(503, "Private financial history could not be saved.") from None
        confirmed = _read_object(client, bucket, object_key, max_bytes)
        if confirmed != encrypted:
            raise HTTPException(503, "Financial history save verification failed.")
        manifest.append({"key": object_key, "sha256": digest,
                         "cipherSha256": hashlib.sha256(encrypted).hexdigest()})
        if len(manifest) > MAX_SHARDS:
            raise HTTPException(413, "Financial history needs partitioning review.")
    return manifest


def read_shards(client, bucket, prefix, manifest, fernet, max_bytes):
    if not isinstance(manifest, list) or len(manifest) > MAX_SHARDS:
        raise HTTPException(503, "Financial history manifest integrity check failed.")
    records = {}
    seen = set()
    for entry in manifest:
        if (not isinstance(entry, dict) or not isinstance(entry.get("key"), str)
                or not entry["key"].startswith(prefix + "/history/")
                or entry["key"] in seen):
            raise HTTPException(503, "Financial history manifest integrity check failed.")
        seen.add(entry["key"])
        raw = _read_object(client, bucket, entry["key"], max_bytes)
        if hashlib.sha256(raw).hexdigest() != entry.get("cipherSha256"):
            raise HTTPException(503, "Encrypted financial history integrity check failed.")
        try:
            body = fernet.decrypt(raw)
            if hashlib.sha256(body).hexdigest() != entry.get("sha256"):
                raise ValueError()
            doc = json.loads(body)
            account, rows = doc["account"], doc["rows"]
            if not isinstance(account, str) or not isinstance(rows, dict):
                raise ValueError()
            result = records.setdefault(account, {})
            if any(key in result for key in rows):
                raise ValueError()
            result.update(rows)
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise HTTPException(503, "Private financial history could not be verified.") from None
    return records


def prune_old_shards(client, bucket, prefix, old_manifest, new_manifest):
    """Best-effort purge after verified root CAS; never delete referenced shards."""
    retained = {entry["key"] for entry in new_manifest}
    for entry in old_manifest or []:
        object_key = entry.get("key") if isinstance(entry, dict) else None
        if not object_key or not object_key.startswith(prefix + "/history/") or object_key in retained:
            continue
        try:
            client.delete_object(Bucket=bucket, Key=object_key)
        except (ClientError, AttributeError):
            # The new manifest is already durable; cleanup can be retried later.
            pass

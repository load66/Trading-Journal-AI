from __future__ import annotations

import math
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import boto3
from botocore.exceptions import ClientError

from config import ConfigError, Settings


R2_STANDARD_FREE_STORAGE_BYTES = 10_000_000_000
R2_STANDARD_FREE_STORAGE_GB_MONTH = 10
R2_STANDARD_STORAGE_PRICE_PER_GB_MONTH = 0.015
R2_STANDARD_CLASS_A_FREE_OPERATIONS = 1_000_000
R2_STANDARD_CLASS_B_FREE_OPERATIONS = 10_000_000
R2_SCREENSHOT_TARGET_BYTES = 500 * 1024
R2_HEALTH_CACHE_SECONDS = 300


def _iso_utc(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


class DiaryStorage:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        if self.settings.storage_mode == "supabase":
            if not self.settings.supabase_url:
                raise ConfigError("SUPABASE_URL is required when STORAGE_MODE=supabase")
            if not self.settings.supabase_secret_key:
                raise ConfigError("SUPABASE_SECRET_KEY is required when STORAGE_MODE=supabase")
        else:
            Path(self.settings.upload_dir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        candidate = (name or "").replace("\\", "/").lstrip("/")
        if not candidate or ".." in candidate.split("/"):
            raise ValueError("Invalid diary object name")
        return candidate

    def _url(self, name: str) -> str:
        name = self._safe_name(name)
        base = self.settings.supabase_url.rstrip("/")
        bucket = quote(self.settings.supabase_storage_bucket, safe="")
        path = quote(name, safe="/")
        return f"{base}/storage/v1/object/{bucket}/{path}"

    def _authenticated_url(self, name: str) -> str:
        name = self._safe_name(name)
        base = self.settings.supabase_url.rstrip("/")
        bucket = quote(self.settings.supabase_storage_bucket, safe="")
        path = quote(name, safe="/")
        return f"{base}/storage/v1/object/authenticated/{bucket}/{path}"

    def _headers(self, content_type: str | None = None) -> dict:
        key = self.settings.supabase_secret_key
        headers = {"apikey": key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def save(self, name: str, data: bytes, content_type: str | None = None) -> str:
        name = self._safe_name(name)
        content_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if self.settings.storage_mode == "local":
            path = Path(self.settings.upload_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return name
        response = httpx.post(
            self._url(name),
            content=data,
            headers={**self._headers(content_type), "x-upsert": "true"},
            timeout=30,
        )
        response.raise_for_status()
        return name

    def read(self, name: str) -> tuple[bytes, str]:
        name = self._safe_name(name)
        if self.settings.storage_mode == "local":
            path = Path(self.settings.upload_dir) / name
            return path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"
        response = httpx.get(self._authenticated_url(name), headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.content, response.headers.get("content-type", "application/octet-stream")

    def delete(self, name: str) -> None:
        name = self._safe_name(name)
        if self.settings.storage_mode == "local":
            path = Path(self.settings.upload_dir) / name
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        response = httpx.delete(self._url(name), headers=self._headers(), timeout=30)
        if response.status_code != 404:
            response.raise_for_status()


class R2Storage:
    """Private Cloudflare R2 storage using its S3-compatible API."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self.bucket = self.settings.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            aws_access_key_id=self.settings.r2_access_key_id,
            aws_secret_access_key=self.settings.r2_secret_access_key,
            region_name="auto",
        )

    @staticmethod
    def _safe_name(name: str) -> str:
        return DiaryStorage._safe_name(name)

    def save(self, name: str, data: bytes, content_type: str | None = None) -> str:
        name = self._safe_name(name)
        content_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.client.put_object(
            Bucket=self.bucket,
            Key=name,
            Body=data,
            ContentType=content_type,
        )
        return name

    def read(self, name: str) -> tuple[bytes, str]:
        name = self._safe_name(name)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=name)
        except ClientError as exc:
            error = exc.response.get("Error", {}) if exc.response else {}
            code = str(error.get("Code", ""))
            status = (exc.response.get("ResponseMetadata", {}) or {}).get("HTTPStatusCode") if exc.response else None
            if code in {"NoSuchKey", "NotFound", "404"} or status == 404:
                raise FileNotFoundError(name) from exc
            raise
        body = response["Body"].read()
        content_type = response.get("ContentType") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        return body, content_type

    def delete(self, name: str) -> None:
        name = self._safe_name(name)
        self.client.delete_object(Bucket=self.bucket, Key=name)

    def usage_summary(self) -> dict:
        """Scan the private bucket with the S3 API and return exact current object usage.

        A ListObjects request is a Class A operation in R2, so callers should cache
        this result rather than polling it continuously.
        """
        total_bytes = 0
        object_count = 0
        latest_modified = None
        storage_classes: dict[str, int] = {}
        continuation = None
        scan_requests = 0

        while True:
            kwargs = {"Bucket": self.bucket, "MaxKeys": 1000}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**kwargs)
            scan_requests += 1

            for item in response.get("Contents") or []:
                try:
                    size = max(0, int(item.get("Size") or 0))
                except (TypeError, ValueError):
                    size = 0
                total_bytes += size
                object_count += 1

                storage_class = str(item.get("StorageClass") or "STANDARD").upper()
                storage_classes[storage_class] = storage_classes.get(storage_class, 0) + 1

                modified = item.get("LastModified")
                if modified is not None and (latest_modified is None or modified > latest_modified):
                    latest_modified = modified

            if not response.get("IsTruncated"):
                break
            continuation = response.get("NextContinuationToken")
            if not continuation:
                break

        return {
            "bucket": self.bucket,
            "object_count": object_count,
            "stored_bytes": total_bytes,
            "average_object_bytes": round(total_bytes / object_count) if object_count else 0,
            "latest_object_at": _iso_utc(latest_modified),
            "storage_classes": storage_classes,
            "scan_class_a_operations": scan_requests,
        }


class ChartStorage:
    """Chart screenshots use R2 when enabled, with legacy storage fallback for reads."""

    def __init__(self, settings: Settings | None = None, legacy_storage: DiaryStorage | None = None):
        self.settings = settings or Settings.from_env()
        self.legacy = legacy_storage or DiaryStorage(self.settings)
        self.primary = R2Storage(self.settings) if self.settings.chart_storage_mode == "r2" else self.legacy
        self._health_cache: dict | None = None
        self._health_cache_at = 0.0

    def save(self, name: str, data: bytes, content_type: str | None = None) -> str:
        return self.primary.save(name, data, content_type)

    def read(self, name: str) -> tuple[bytes, str]:
        if self.primary is self.legacy:
            return self.legacy.read(name)
        try:
            return self.primary.read(name)
        except FileNotFoundError:
            return self.legacy.read(name)

    def delete(self, name: str) -> None:
        if self.primary is self.legacy:
            self.legacy.delete(name)
            return
        # R2 delete is idempotent. Also clean up a possible legacy copy so old
        # Supabase screenshots do not become orphaned after replacement/removal.
        self.primary.delete(name)
        try:
            self.legacy.delete(name)
        except Exception:
            pass

    def health(self, force: bool = False) -> dict:
        checked_at = datetime.now(timezone.utc).isoformat()
        if self.primary is self.legacy:
            return {
                "enabled": False,
                "provider": "Cloudflare R2",
                "mode": self.settings.chart_storage_mode,
                "status": "disabled",
                "checked_at": checked_at,
                "message": "Cloudflare R2 is not the active chart screenshot storage.",
            }

        now = time.monotonic()
        if (
            not force
            and self._health_cache is not None
            and now - self._health_cache_at < R2_HEALTH_CACHE_SECONDS
        ):
            return {
                **self._health_cache,
                "cached": True,
                "cache_age_seconds": round(now - self._health_cache_at),
            }

        try:
            usage = self.primary.usage_summary()
        except Exception:
            return {
                "enabled": True,
                "provider": "Cloudflare R2",
                "mode": "r2",
                "bucket": self.settings.r2_bucket,
                "status": "error",
                "checked_at": checked_at,
                "cached": False,
                "message": "Could not read Cloudflare R2 bucket usage.",
            }

        stored_bytes = int(usage["stored_bytes"])
        current_gb = stored_bytes / 1_000_000_000
        used_percent = (stored_bytes / R2_STANDARD_FREE_STORAGE_BYTES) * 100
        remaining_bytes = max(0, R2_STANDARD_FREE_STORAGE_BYTES - stored_bytes)
        screenshot_capacity = math.floor(remaining_bytes / R2_SCREENSHOT_TARGET_BYTES)
        rounded_gb_month = math.ceil(current_gb) if current_gb > 0 else 0
        billable_gb_month = max(0, rounded_gb_month - R2_STANDARD_FREE_STORAGE_GB_MONTH)
        projected_cost = round(billable_gb_month * R2_STANDARD_STORAGE_PRICE_PER_GB_MONTH, 2)

        classes = usage.get("storage_classes") or {}
        nonstandard_objects = sum(
            count for storage_class, count in classes.items()
            if storage_class not in {"STANDARD"}
        )
        free_tier_applicable = nonstandard_objects == 0

        if not free_tier_applicable:
            status = "warning"
        elif used_percent >= 100:
            status = "billable"
        elif used_percent >= 90:
            status = "warning"
        elif used_percent >= 75:
            status = "watch"
        else:
            status = "healthy"

        result = {
            "enabled": True,
            "provider": "Cloudflare R2",
            "mode": "r2",
            "bucket": usage["bucket"],
            "status": status,
            "checked_at": checked_at,
            "cached": False,
            "cache_ttl_seconds": R2_HEALTH_CACHE_SECONDS,
            "object_count": usage["object_count"],
            "stored_bytes": stored_bytes,
            "stored_gb": round(current_gb, 4),
            "average_object_bytes": usage["average_object_bytes"],
            "latest_object_at": usage["latest_object_at"],
            "storage_classes": classes,
            "standard_free_tier_applicable": free_tier_applicable,
            "free_storage_bytes": R2_STANDARD_FREE_STORAGE_BYTES,
            "free_storage_gb_month": R2_STANDARD_FREE_STORAGE_GB_MONTH,
            "storage_used_percent": round(used_percent, 3),
            "remaining_free_bytes": remaining_bytes,
            "estimated_500kb_screenshots_remaining": screenshot_capacity,
            "projected_storage_cost_usd_if_held_month": projected_cost,
            "projected_cost_note": (
                "Snapshot estimate only. R2 storage billing uses average daily peak storage over the billing month."
            ),
            "class_a_free_operations": R2_STANDARD_CLASS_A_FREE_OPERATIONS,
            "class_b_free_operations": R2_STANDARD_CLASS_B_FREE_OPERATIONS,
            "operation_usage_available": False,
            "operation_usage_reason": (
                "Monthly Class A/B usage requires Cloudflare account analytics credentials; "
                "the current S3 access keys expose bucket objects but not monthly billing counters."
            ),
            "health_scan_class_a_operations": usage["scan_class_a_operations"],
            "pricing_as_of": "2026-08-07",
            "pricing_url": "https://developers.cloudflare.com/r2/pricing/",
        }
        self._health_cache = result
        self._health_cache_at = now
        return result

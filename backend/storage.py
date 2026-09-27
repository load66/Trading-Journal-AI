from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx
import boto3
from botocore.exceptions import ClientError

from config import ConfigError, Settings


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


class ChartStorage:
    """Chart screenshots use R2 when enabled, with legacy storage fallback for reads."""

    def __init__(self, settings: Settings | None = None, legacy_storage: DiaryStorage | None = None):
        self.settings = settings or Settings.from_env()
        self.legacy = legacy_storage or DiaryStorage(self.settings)
        self.primary = R2Storage(self.settings) if self.settings.chart_storage_mode == "r2" else self.legacy

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

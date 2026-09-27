from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx

from config import ConfigError, Settings


class DiaryStorage:
    """Provider-neutral object storage facade.

    The active provider is selected by STORAGE_MODE, but callers may pass an
    explicit provider when reading/deleting older objects after a provider
    migration. This keeps screenshot metadata portable across Supabase, R2/S3,
    and local development storage.
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.from_env()
        self._s3_client = None
        self._validate_provider(self.settings.storage_mode)
        if self.settings.storage_mode == "local":
            Path(self.settings.upload_dir).mkdir(parents=True, exist_ok=True)

    @property
    def current_provider(self) -> str:
        return self.settings.storage_mode

    @property
    def current_provider_label(self) -> str:
        if self.settings.storage_mode == "s3":
            if "r2.cloudflarestorage.com" in self.settings.s3_endpoint_url:
                return "Cloudflare R2"
            return "S3-compatible"
        if self.settings.storage_mode == "supabase":
            return "Supabase Storage"
        return "Local storage"

    def _validate_provider(self, provider: str) -> None:
        if provider == "local":
            return
        if provider == "supabase":
            if not self.settings.supabase_url:
                raise ConfigError("SUPABASE_URL is required for Supabase storage")
            if not self.settings.supabase_secret_key:
                raise ConfigError("SUPABASE_SECRET_KEY is required for Supabase storage")
            return
        if provider == "s3":
            required = {
                "S3_ENDPOINT_URL": self.settings.s3_endpoint_url,
                "S3_ACCESS_KEY_ID": self.settings.s3_access_key_id,
                "S3_SECRET_ACCESS_KEY": self.settings.s3_secret_access_key,
                "S3_BUCKET": self.settings.s3_bucket,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ConfigError(f"{', '.join(missing)} required for S3 storage")
            return
        raise ConfigError(f"Unsupported storage provider: {provider}")

    @staticmethod
    def _safe_name(name: str) -> str:
        candidate = (name or "").replace("\\", "/").lstrip("/")
        if not candidate or ".." in candidate.split("/"):
            raise ValueError("Invalid diary object name")
        return candidate

    def _provider(self, provider: str | None) -> str:
        selected = provider or self.settings.storage_mode
        self._validate_provider(selected)
        return selected

    def _supabase_url(self, name: str) -> str:
        name = self._safe_name(name)
        base = self.settings.supabase_url.rstrip("/")
        bucket = quote(self.settings.supabase_storage_bucket, safe="")
        path = quote(name, safe="/")
        return f"{base}/storage/v1/object/{bucket}/{path}"

    def _supabase_authenticated_url(self, name: str) -> str:
        name = self._safe_name(name)
        base = self.settings.supabase_url.rstrip("/")
        bucket = quote(self.settings.supabase_storage_bucket, safe="")
        path = quote(name, safe="/")
        return f"{base}/storage/v1/object/authenticated/{bucket}/{path}"

    def _supabase_headers(self, content_type: str | None = None) -> dict:
        key = self.settings.supabase_secret_key
        headers = {"apikey": key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _s3(self):
        if self._s3_client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig
            except ImportError as exc:
                raise ConfigError("boto3 is required when STORAGE_MODE=s3") from exc
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.settings.s3_endpoint_url,
                aws_access_key_id=self.settings.s3_access_key_id,
                aws_secret_access_key=self.settings.s3_secret_access_key,
                region_name=self.settings.s3_region,
                config=BotoConfig(signature_version="s3v4"),
            )
        return self._s3_client

    def save(
        self,
        name: str,
        data: bytes,
        content_type: str | None = None,
        provider: str | None = None,
    ) -> str:
        name = self._safe_name(name)
        selected = self._provider(provider)
        content_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"

        if selected == "local":
            path = Path(self.settings.upload_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return name

        if selected == "supabase":
            response = httpx.post(
                self._supabase_url(name),
                content=data,
                headers={**self._supabase_headers(content_type), "x-upsert": "true"},
                timeout=30,
            )
            response.raise_for_status()
            return name

        self._s3().put_object(
            Bucket=self.settings.s3_bucket,
            Key=name,
            Body=data,
            ContentType=content_type,
        )
        return name

    def read(self, name: str, provider: str | None = None) -> tuple[bytes, str]:
        name = self._safe_name(name)
        selected = self._provider(provider)

        if selected == "local":
            path = Path(self.settings.upload_dir) / name
            return path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"

        if selected == "supabase":
            response = httpx.get(
                self._supabase_authenticated_url(name),
                headers=self._supabase_headers(),
                timeout=30,
            )
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "application/octet-stream")

        response = self._s3().get_object(Bucket=self.settings.s3_bucket, Key=name)
        body = response["Body"].read()
        content_type = response.get("ContentType") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        return body, content_type

    def delete(self, name: str, provider: str | None = None) -> None:
        name = self._safe_name(name)
        selected = self._provider(provider)

        if selected == "local":
            path = Path(self.settings.upload_dir) / name
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return

        if selected == "supabase":
            response = httpx.delete(
                self._supabase_url(name),
                headers=self._supabase_headers(),
                timeout=30,
            )
            if response.status_code != 404:
                response.raise_for_status()
            return

        self._s3().delete_object(Bucket=self.settings.s3_bucket, Key=name)

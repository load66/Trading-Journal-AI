from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

import httpx

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

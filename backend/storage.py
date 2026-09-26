from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import mimetypes
import re
import uuid

from runtime_config import RuntimeConfig, load_runtime_config

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_diary_object_path(
    date: str,
    account_id: int,
    filename: str,
    *,
    suffix: str | None = None,
) -> str:
    """Create a unique storage path without trusting the uploaded filename."""
    basename = Path(filename or "upload").name
    stem = Path(basename).stem or "upload"
    ext = suffix if suffix is not None else Path(basename).suffix.lower()
    if ext and not ext.startswith("."):
        ext = "." + ext
    safe_stem = _SAFE_CHARS.sub("_", stem).strip("._-") or "upload"
    token = uuid.uuid4().hex[:12]
    return f"{date}/{int(account_id)}/{token}_{safe_stem}{ext.lower()}"


class LocalDiaryStorage:
    def __init__(self, upload_dir: str):
        self.root = Path(upload_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        path: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path

    def signed_url(self, path: str, expires_in: int = 900):
        return None

    def delete(self, path: str) -> None:
        target = self.root / path
        try:
            target.unlink()
        except FileNotFoundError:
            return

        parent = target.parent
        while parent != self.root and self.root in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def local_path(self, path: str):
        return self.root / path


class SupabaseDiaryStorage:
    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket_name = bucket

    @property
    def bucket(self):
        return self.client.storage.from_(self.bucket_name)

    def save(
        self,
        path: str,
        data: bytes,
        content_type: str | None = None,
    ) -> str:
        options = {"upsert": "false"}
        if content_type:
            options["content-type"] = content_type
        self.bucket.upload(path, data, options)
        return path

    def signed_url(self, path: str, expires_in: int = 900):
        response = self.bucket.create_signed_url(path, expires_in)
        if isinstance(response, dict):
            url = (
                response.get("signedURL")
                or response.get("signedUrl")
                or response.get("signed_url")
            )
            if url:
                return url

        for attr in ("signed_url", "signedURL", "signedUrl"):
            value = getattr(response, attr, None)
            if value:
                return value

        raise RuntimeError("Supabase did not return a signed URL")

    def delete(self, path: str) -> None:
        self.bucket.remove([path])

    def local_path(self, path: str):
        return None


def _create_supabase_client(url: str, secret_key: str):
    # Lazy import keeps the original local-only workflow lightweight.
    from supabase import create_client

    return create_client(url, secret_key)


@lru_cache(maxsize=4)
def _cached_local(upload_dir: str):
    return LocalDiaryStorage(upload_dir)


@lru_cache(maxsize=4)
def _cached_supabase(url: str, secret_key: str, bucket: str):
    return SupabaseDiaryStorage(
        _create_supabase_client(url, secret_key),
        bucket,
    )


def get_diary_storage(config: RuntimeConfig | None = None):
    cfg = config or load_runtime_config()
    if cfg.storage_mode == "local":
        return _cached_local(cfg.upload_dir)
    return _cached_supabase(
        cfg.supabase_url,
        cfg.supabase_secret_key,
        cfg.supabase_storage_bucket,
    )


def content_type_for(
    path: str,
    fallback: str = "application/octet-stream",
) -> str:
    return mimetypes.guess_type(path)[0] or fallback

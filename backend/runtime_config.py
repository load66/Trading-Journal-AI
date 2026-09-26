from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration is unsafe or incomplete."""


def _clean(value: str | None, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


@dataclass(frozen=True)
class RuntimeConfig:
    app_env: str
    auth_mode: str
    database_mode: str
    database_path: str
    storage_mode: str
    upload_dir: str
    frontend_origins: tuple[str, ...]
    authorized_user_id: str
    supabase_url: str
    supabase_secret_key: str
    supabase_storage_bucket: str
    turso_database_url: str
    turso_auth_token: str

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


def load_runtime_config(env: Mapping[str, str] | None = None) -> RuntimeConfig:
    source = os.environ if env is None else env

    def get(name: str, default: str = "") -> str:
        return _clean(source.get(name), default)

    app_env = get("APP_ENV", "local").lower()
    auth_mode = get("AUTH_MODE", "disabled").lower()
    database_mode = get("DATABASE_MODE", "sqlite").lower()
    storage_mode = get("STORAGE_MODE", "local").lower()

    allowed = {
        "APP_ENV": (app_env, {"local", "production"}),
        "AUTH_MODE": (auth_mode, {"disabled", "supabase"}),
        "DATABASE_MODE": (database_mode, {"sqlite", "turso"}),
        "STORAGE_MODE": (storage_mode, {"local", "supabase"}),
    }
    for name, (value, choices) in allowed.items():
        if value not in choices:
            raise ConfigurationError(
                f"{name} must be one of {', '.join(sorted(choices))}; got {value!r}"
            )

    frontend_origins = tuple(
        origin.strip()
        for origin in get("FRONTEND_ORIGINS").split(",")
        if origin.strip()
    )

    cfg = RuntimeConfig(
        app_env=app_env,
        auth_mode=auth_mode,
        database_mode=database_mode,
        database_path=get("DATABASE_PATH", "trading_journal.db"),
        storage_mode=storage_mode,
        upload_dir=get("UPLOAD_DIR", "uploads"),
        frontend_origins=frontend_origins,
        authorized_user_id=get("AUTHORIZED_USER_ID"),
        supabase_url=get("SUPABASE_URL").rstrip("/"),
        supabase_secret_key=get("SUPABASE_SECRET_KEY"),
        supabase_storage_bucket=get(
            "SUPABASE_STORAGE_BUCKET", "trading-journal-diary"
        ),
        turso_database_url=get("TURSO_DATABASE_URL"),
        turso_auth_token=get("TURSO_AUTH_TOKEN"),
    )

    if cfg.is_production:
        if cfg.auth_mode != "supabase":
            raise ConfigurationError("AUTH_MODE must be supabase in production")
        if cfg.database_mode != "turso":
            raise ConfigurationError("DATABASE_MODE must be turso in production")
        if cfg.storage_mode != "supabase":
            raise ConfigurationError("STORAGE_MODE must be supabase in production")

        required = {
            "AUTHORIZED_USER_ID": cfg.authorized_user_id,
            "SUPABASE_URL": cfg.supabase_url,
            "SUPABASE_SECRET_KEY": cfg.supabase_secret_key,
            "SUPABASE_STORAGE_BUCKET": cfg.supabase_storage_bucket,
            "TURSO_DATABASE_URL": cfg.turso_database_url,
            "TURSO_AUTH_TOKEN": cfg.turso_auth_token,
            "FRONTEND_ORIGINS": ",".join(cfg.frontend_origins),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                "Missing required production configuration: " + ", ".join(missing)
            )
        if not cfg.supabase_url.startswith("https://"):
            raise ConfigurationError(
                "SUPABASE_URL must use https:// in production"
            )
        insecure = [
            origin
            for origin in cfg.frontend_origins
            if not origin.startswith("https://")
        ]
        if insecure:
            raise ConfigurationError(
                "FRONTEND_ORIGINS must use https:// in production"
            )

    return cfg

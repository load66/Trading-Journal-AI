from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


class ConfigError(RuntimeError):
    """Raised when deployment configuration is invalid or unsafe."""


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip().rstrip('/') for part in value.split(',') if part.strip())


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    app_env: str = 'development'
    auth_mode: str = 'disabled'
    supabase_url: str = ''
    allowed_user_id: str = ''
    supabase_jwt_audience: str = 'authenticated'
    frontend_origins: tuple[str, ...] = ()
    database_mode: str = 'sqlite'
    database_path: str = 'trading_journal.db'
    turso_database_url: str = ''
    turso_auth_token: str = ''
    storage_mode: str = 'local'
    upload_dir: str = 'uploads'
    supabase_secret_key: str = ''
    supabase_storage_bucket: str = 'diary'
    max_diary_upload_bytes: int = 10 * 1024 * 1024
    max_csv_upload_bytes: int = 20 * 1024 * 1024

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> 'Settings':
        env = os.environ if environ is None else environ
        app_env = env.get('APP_ENV', 'development').strip().lower()
        auth_mode = env.get('AUTH_MODE', 'disabled').strip().lower()
        database_mode = env.get('DATABASE_MODE', 'sqlite').strip().lower()
        storage_mode = env.get('STORAGE_MODE', 'local').strip().lower()

        if app_env not in {'development', 'test', 'production'}:
            raise ConfigError('APP_ENV must be development, test, or production')
        if auth_mode not in {'disabled', 'supabase'}:
            raise ConfigError('AUTH_MODE must be disabled or supabase')
        if database_mode not in {'sqlite', 'turso'}:
            raise ConfigError('DATABASE_MODE must be sqlite or turso')
        if storage_mode not in {'local', 'supabase'}:
            raise ConfigError('STORAGE_MODE must be local or supabase')

        turso_database_url = env.get('TURSO_DATABASE_URL', '').strip()
        turso_auth_token = env.get('TURSO_AUTH_TOKEN', '').strip()
        if database_mode == 'turso':
            if not turso_database_url:
                raise ConfigError('TURSO_DATABASE_URL is required when DATABASE_MODE=turso')
            if not turso_auth_token:
                raise ConfigError('TURSO_AUTH_TOKEN is required when DATABASE_MODE=turso')

        supabase_url = env.get('SUPABASE_URL', '').strip().rstrip('/')
        allowed_user_id = env.get('ALLOWED_USER_ID', '').strip()
        if auth_mode == 'supabase':
            if not supabase_url:
                raise ConfigError('SUPABASE_URL is required when AUTH_MODE=supabase')
            if not allowed_user_id:
                raise ConfigError('ALLOWED_USER_ID is required when AUTH_MODE=supabase')

        return cls(
            app_env=app_env,
            auth_mode=auth_mode,
            supabase_url=supabase_url,
            allowed_user_id=allowed_user_id,
            supabase_jwt_audience=env.get('SUPABASE_JWT_AUDIENCE', 'authenticated').strip() or 'authenticated',
            frontend_origins=_csv(env.get('FRONTEND_ORIGINS', '')),
            database_mode=database_mode,
            database_path=env.get('DATABASE_PATH', 'trading_journal.db').strip() or 'trading_journal.db',
            turso_database_url=turso_database_url,
            turso_auth_token=turso_auth_token,
            storage_mode=storage_mode,
            upload_dir=env.get('UPLOAD_DIR', 'uploads').strip() or 'uploads',
            supabase_secret_key=(env.get('SUPABASE_SECRET_KEY', '') or env.get('SUPABASE_SERVICE_ROLE_KEY', '')).strip(),
            supabase_storage_bucket=env.get('SUPABASE_STORAGE_BUCKET', 'diary').strip() or 'diary',
            max_diary_upload_bytes=_positive_int(
                env.get('MAX_DIARY_UPLOAD_BYTES', str(10 * 1024 * 1024)),
                'MAX_DIARY_UPLOAD_BYTES',
            ),
            max_csv_upload_bytes=_positive_int(
                env.get('MAX_CSV_UPLOAD_BYTES', str(20 * 1024 * 1024)),
                'MAX_CSV_UPLOAD_BYTES',
            ),
        )

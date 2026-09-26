import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def load_with(overrides):
    from runtime_config import load_runtime_config
    env = {
        "APP_ENV": "local",
        "AUTH_MODE": "disabled",
        "DATABASE_MODE": "sqlite",
        "DATABASE_PATH": "trading_journal.db",
        "STORAGE_MODE": "local",
        "UPLOAD_DIR": "uploads",
    }
    env.update(overrides)
    return load_runtime_config(env)


def production_env():
    return {
        "APP_ENV": "production",
        "AUTH_MODE": "supabase",
        "AUTHORIZED_USER_ID": "11111111-1111-1111-1111-111111111111",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "test-server-secret",
        "SUPABASE_STORAGE_BUCKET": "trading-journal-diary",
        "DATABASE_MODE": "turso",
        "TURSO_DATABASE_URL": "https://example.turso.io",
        "TURSO_AUTH_TOKEN": "test-turso-token",
        "STORAGE_MODE": "supabase",
        "FRONTEND_ORIGINS": "https://load66.github.io",
    }


def test_local_defaults_preserve_current_local_first_behavior():
    cfg = load_with({})
    assert cfg.app_env == "local"
    assert cfg.auth_mode == "disabled"
    assert cfg.database_mode == "sqlite"
    assert cfg.database_path == "trading_journal.db"
    assert cfg.storage_mode == "local"
    assert cfg.upload_dir == "uploads"


def test_valid_production_configuration_is_accepted():
    cfg = load_with(production_env())
    assert cfg.is_production is True
    assert cfg.auth_mode == "supabase"
    assert cfg.database_mode == "turso"
    assert cfg.storage_mode == "supabase"
    assert cfg.authorized_user_id == "11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    "missing",
    [
        "AUTHORIZED_USER_ID",
        "SUPABASE_URL",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_STORAGE_BUCKET",
        "TURSO_DATABASE_URL",
        "TURSO_AUTH_TOKEN",
        "FRONTEND_ORIGINS",
    ],
)
def test_production_missing_required_value_fails_closed(missing):
    from runtime_config import ConfigurationError
    env = production_env()
    env.pop(missing)
    with pytest.raises(ConfigurationError, match=missing):
        load_with(env)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("AUTH_MODE", "disabled", "AUTH_MODE"),
        ("DATABASE_MODE", "sqlite", "DATABASE_MODE"),
        ("STORAGE_MODE", "local", "STORAGE_MODE"),
    ],
)
def test_production_rejects_local_fallback_modes(key, value, message):
    from runtime_config import ConfigurationError
    env = production_env()
    env[key] = value
    with pytest.raises(ConfigurationError, match=message):
        load_with(env)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AUTH_MODE", "unknown"),
        ("DATABASE_MODE", "postgres"),
        ("STORAGE_MODE", "s3"),
        ("APP_ENV", "mystery"),
    ],
)
def test_unknown_modes_are_rejected_in_every_environment(key, value):
    from runtime_config import ConfigurationError
    env = {"APP_ENV": "local"}
    env[key] = value
    with pytest.raises(ConfigurationError):
        load_with(env)

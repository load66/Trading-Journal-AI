import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from config import ConfigError, Settings  # noqa: E402
from storage import DiaryStorage  # noqa: E402


def cfg(tmp_path, **overrides):
    env = {
        "APP_ENV": "test",
        "AUTH_MODE": "disabled",
        "DATABASE_MODE": "sqlite",
        "DATABASE_PATH": ":memory:",
        "STORAGE_MODE": "local",
        "UPLOAD_DIR": str(tmp_path),
    }
    env.update(overrides)
    return Settings.from_env(env)


def test_local_storage_round_trip(tmp_path):
    store = DiaryStorage(cfg(tmp_path))
    store.save("entry.jpg", b"abc", "image/jpeg")
    assert store.read("entry.jpg") == (b"abc", "image/jpeg")
    store.delete("entry.jpg")
    assert not (tmp_path / "entry.jpg").exists()


def test_supabase_storage_requires_server_credentials(tmp_path):
    settings = cfg(tmp_path, STORAGE_MODE="supabase", SUPABASE_URL="https://p.supabase.co")
    with pytest.raises(ConfigError, match="SUPABASE_SERVICE_ROLE_KEY"):
        DiaryStorage(settings)


def test_object_names_cannot_escape_storage_root(tmp_path):
    store = DiaryStorage(cfg(tmp_path))
    with pytest.raises(ValueError):
        store.save("../secret", b"x", "application/octet-stream")

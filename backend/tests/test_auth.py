import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from config import ConfigError, Settings  # noqa: E402


def settings(**overrides):
    env = {
        "APP_ENV": "test",
        "AUTH_MODE": "disabled",
        "DATABASE_MODE": "sqlite",
        "DATABASE_PATH": ":memory:",
        "STORAGE_MODE": "local",
    }
    env.update(overrides)
    return Settings.from_env(env)


def fresh_main(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for key in [
        "AUTH_MODE",
        "SUPABASE_URL",
        "ALLOWED_USER_ID",
        "SUPABASE_JWT_AUDIENCE",
        "APP_ENV",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)

    import main
    return main


def test_auth_disabled_by_default():
    from auth import authenticate_bearer

    claims = authenticate_bearer(None, settings())
    assert claims == {"sub": "local-development"}


def test_auth_required_missing_bearer_is_401():
    from auth import AuthenticationError, authenticate_bearer

    cfg = settings(
        AUTH_MODE="supabase",
        SUPABASE_URL="https://project.supabase.co",
        ALLOWED_USER_ID="owner-uuid",
    )
    with pytest.raises(AuthenticationError, match="Authentication required"):
        authenticate_bearer(None, cfg)


def test_auth_required_wrong_user_is_403():
    from auth import AuthorizationError, authenticate_bearer

    cfg = settings(
        AUTH_MODE="supabase",
        SUPABASE_URL="https://project.supabase.co",
        ALLOWED_USER_ID="owner-uuid",
    )
    with pytest.raises(AuthorizationError):
        authenticate_bearer(
            "Bearer valid-token",
            cfg,
            decoder=lambda token, config: {"sub": "someone-else"},
        )


def test_auth_required_authorized_user_passes():
    from auth import authenticate_bearer

    cfg = settings(
        AUTH_MODE="supabase",
        SUPABASE_URL="https://project.supabase.co",
        ALLOWED_USER_ID="owner-uuid",
    )
    expected = {"sub": "owner-uuid", "aud": "authenticated"}
    assert authenticate_bearer(
        "Bearer valid-token",
        cfg,
        decoder=lambda token, config: expected,
    ) == expected


def test_required_auth_config_fails_closed():
    with pytest.raises(ConfigError, match="SUPABASE_URL"):
        settings(AUTH_MODE="supabase", SUPABASE_URL="", ALLOWED_USER_ID="")


def test_health_is_public_but_api_requires_auth(monkeypatch, tmp_path):
    main = fresh_main(
        monkeypatch,
        tmp_path,
        AUTH_MODE="supabase",
        SUPABASE_URL="https://project.supabase.co",
        ALLOWED_USER_ID="owner-uuid",
    )

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/accounts").status_code == 401


def test_authorized_request_reaches_api(monkeypatch, tmp_path):
    main = fresh_main(
        monkeypatch,
        tmp_path,
        AUTH_MODE="supabase",
        SUPABASE_URL="https://project.supabase.co",
        ALLOWED_USER_ID="owner-uuid",
    )
    auth = sys.modules["auth"]
    monkeypatch.setattr(
        auth,
        "decode_supabase_token",
        lambda token, config: {"sub": "owner-uuid", "aud": "authenticated"},
    )

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        response = client.get("/api/accounts", headers={"Authorization": "Bearer valid"})
        assert response.status_code == 200


def test_production_500_response_is_sanitized(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path, APP_ENV="production")
    response = asyncio.run(
        main.global_exception_handler(None, RuntimeError("database password leaked"))
    )
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload == {"error": "Internal server error"}
    assert "password" not in response.body.decode().lower()

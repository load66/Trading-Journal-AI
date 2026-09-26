import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def fresh_auth(monkeypatch, **env):
    for key in [
        "AUTH_REQUIRED",
        "SUPABASE_URL",
        "AUTHORIZED_USER_ID",
        "APP_ENV",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("auth", None)
    import auth
    return auth


def fresh_main(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    for key in [
        "AUTH_REQUIRED",
        "SUPABASE_URL",
        "AUTHORIZED_USER_ID",
        "APP_ENV",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)

    import main
    return main


def test_auth_disabled_by_default(monkeypatch):
    auth = fresh_auth(monkeypatch)
    assert auth.auth_required() is False
    assert auth.authorize_header(None) == {}


def test_auth_required_missing_bearer_is_401(monkeypatch):
    auth = fresh_auth(
        monkeypatch,
        AUTH_REQUIRED="true",
        SUPABASE_URL="https://project.supabase.co",
        AUTHORIZED_USER_ID="owner-uuid",
    )
    with pytest.raises(auth.AuthError) as exc:
        auth.authorize_header(None)
    assert exc.value.status_code == 401


def test_auth_required_wrong_user_is_403(monkeypatch):
    auth = fresh_auth(
        monkeypatch,
        AUTH_REQUIRED="true",
        SUPABASE_URL="https://project.supabase.co",
        AUTHORIZED_USER_ID="owner-uuid",
    )
    monkeypatch.setattr(auth, "verify_access_token", lambda token: {"sub": "someone-else"})

    with pytest.raises(auth.AuthError) as exc:
        auth.authorize_header("Bearer valid-token")
    assert exc.value.status_code == 403


def test_auth_required_authorized_user_passes(monkeypatch):
    auth = fresh_auth(
        monkeypatch,
        AUTH_REQUIRED="true",
        SUPABASE_URL="https://project.supabase.co",
        AUTHORIZED_USER_ID="owner-uuid",
    )
    expected = {"sub": "owner-uuid", "aud": "authenticated"}
    monkeypatch.setattr(auth, "verify_access_token", lambda token: expected)

    assert auth.authorize_header("Bearer valid-token") == expected


def test_required_auth_config_fails_closed(monkeypatch):
    auth = fresh_auth(monkeypatch, AUTH_REQUIRED="true")
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        auth.validate_auth_config()


def test_health_is_public_but_api_requires_auth(monkeypatch, tmp_path):
    main = fresh_main(
        monkeypatch,
        tmp_path,
        AUTH_REQUIRED="true",
        SUPABASE_URL="https://project.supabase.co",
        AUTHORIZED_USER_ID="owner-uuid",
    )

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        health = client.get("/")
        assert health.status_code == 200

        protected = client.get("/api/accounts")
        assert protected.status_code == 401


def test_authorized_request_reaches_api(monkeypatch, tmp_path):
    main = fresh_main(
        monkeypatch,
        tmp_path,
        AUTH_REQUIRED="true",
        SUPABASE_URL="https://project.supabase.co",
        AUTHORIZED_USER_ID="owner-uuid",
    )
    monkeypatch.setattr(main, "authorize_header", lambda header: {"sub": "owner-uuid"})

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        response = client.get("/api/accounts", headers={"Authorization": "Bearer valid"})
        assert response.status_code == 200


def test_production_500_response_is_sanitized(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path, APP_ENV="production")
    response = asyncio.run(main.global_exception_handler(None, RuntimeError("database password leaked")))
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload == {"error": "Internal server error"}
    assert "password" not in response.body.decode().lower()

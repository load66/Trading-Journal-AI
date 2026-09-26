import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from auth import AuthenticationError, SingleUserAuthMiddleware
from runtime_config import load_runtime_config


def config(auth_mode="supabase"):
    return load_runtime_config({
        "APP_ENV": "local",
        "AUTH_MODE": auth_mode,
        "AUTHORIZED_USER_ID": "owner-user",
        "SUPABASE_URL": "https://example.supabase.co",
    })


def make_client(verifier=lambda token, cfg: {"sub": "owner-user"}, auth_mode="supabase"):
    app = FastAPI()
    app.add_middleware(
        SingleUserAuthMiddleware,
        config_loader=lambda: config(auth_mode),
        verifier=verifier,
    )

    @app.get("/")
    def health():
        return {"status": "ok"}

    @app.get("/api/private")
    def private():
        return {"status": "private"}

    return TestClient(app)


def test_health_endpoint_stays_public():
    assert make_client().get("/").status_code == 200


def test_auth_disabled_preserves_local_api_access():
    assert make_client(auth_mode="disabled").get("/api/private").status_code == 200


def test_missing_bearer_token_is_401():
    response = make_client().get("/api/private")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token_is_401():
    def invalid(token, cfg):
        raise AuthenticationError("invalid")

    response = make_client(invalid).get(
        "/api/private",
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401


def test_valid_token_for_other_user_is_403():
    response = make_client(lambda token, cfg: {"sub": "other-user"}).get(
        "/api/private",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 403


def test_authorized_owner_reaches_route():
    response = make_client().get(
        "/api/private",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "private"}


def test_options_preflight_is_not_blocked_by_auth():
    # The tiny test route has no OPTIONS handler, so 405 proves auth did not
    # convert the preflight into an authentication error.
    assert make_client().options("/api/private").status_code != 401

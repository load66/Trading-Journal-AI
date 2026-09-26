import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from config import ConfigError, Settings  # noqa: E402
from auth import authenticate_bearer, install_auth_middleware  # noqa: E402


def make_settings(**overrides):
    env = {
        'APP_ENV': 'development',
        'AUTH_MODE': 'disabled',
        'SUPABASE_URL': '',
        'ALLOWED_USER_ID': '',
        'SUPABASE_JWT_AUDIENCE': 'authenticated',
        'FRONTEND_ORIGINS': '',
        'DATABASE_MODE': 'sqlite',
        'DATABASE_PATH': 'trading_journal.db',
        'STORAGE_MODE': 'local',
        'UPLOAD_DIR': 'uploads',
    }
    env.update(overrides)
    return Settings.from_env(env)


def make_app(settings, decoder=None):
    app = FastAPI()
    install_auth_middleware(app, settings, decoder=decoder)

    @app.get('/')
    def health():
        return {'status': 'ok'}

    @app.get('/api/private')
    def private():
        return {'private': True}

    return TestClient(app)


def test_disabled_auth_allows_api_request():
    client = make_app(make_settings(AUTH_MODE='disabled'))
    response = client.get('/api/private')
    assert response.status_code == 200


def test_supabase_mode_rejects_missing_bearer():
    client = make_app(make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    ))
    response = client.get('/api/private')
    assert response.status_code == 401
    assert response.json() == {'error': 'Authentication required'}


def test_supabase_mode_rejects_invalid_token():
    def invalid_decoder(token, settings):
        raise ValueError('bad token')

    client = make_app(make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    ), decoder=invalid_decoder)
    response = client.get('/api/private', headers={'Authorization': 'Bearer nope'})
    assert response.status_code == 401
    assert response.json() == {'error': 'Invalid or expired authentication token'}


def test_supabase_mode_allows_configured_subject():
    settings = make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    )
    claims = authenticate_bearer(
        'Bearer valid',
        settings,
        decoder=lambda token, settings: {'sub': 'owner-123', 'aud': 'authenticated'},
    )
    assert claims['sub'] == 'owner-123'


def test_supabase_mode_forbids_other_subject():
    client = make_app(make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    ), decoder=lambda token, settings: {'sub': 'intruder'})
    response = client.get('/api/private', headers={'Authorization': 'Bearer valid'})
    assert response.status_code == 403
    assert response.json() == {'error': 'This account is not authorized for this journal'}


def test_options_preflight_bypasses_auth():
    client = make_app(make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    ))
    response = client.options('/api/private')
    assert response.status_code != 401


def test_health_endpoint_bypasses_auth():
    client = make_app(make_settings(
        AUTH_MODE='supabase',
        SUPABASE_URL='https://example.supabase.co',
        ALLOWED_USER_ID='owner-123',
    ))
    response = client.get('/')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_supabase_mode_requires_url_and_allowed_user():
    with pytest.raises(ConfigError, match='SUPABASE_URL'):
        make_settings(AUTH_MODE='supabase', SUPABASE_URL='', ALLOWED_USER_ID='owner-123')
    with pytest.raises(ConfigError, match='ALLOWED_USER_ID'):
        make_settings(
            AUTH_MODE='supabase',
            SUPABASE_URL='https://example.supabase.co',
            ALLOWED_USER_ID='',
        )

from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from jwt import PyJWKClient

from config import Settings


class AuthenticationError(Exception):
    pass


class AuthorizationError(Exception):
    pass


_ALLOWED_ALGORITHMS = ('RS256', 'ES256')


def decode_supabase_token(token: str, settings: Settings) -> dict:
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    try:
        key = PyJWKClient(jwks_url).get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=list(_ALLOWED_ALGORITHMS),
            audience=settings.supabase_jwt_audience,
            issuer=f"{settings.supabase_url}/auth/v1",
            options={'require': ['exp', 'sub']},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError('Invalid or expired authentication token') from exc
    except Exception as exc:
        raise AuthenticationError('Invalid or expired authentication token') from exc


def authenticate_bearer(
    authorization: str | None,
    settings: Settings,
    decoder: Callable[[str, Settings], dict] | None = None,
) -> dict:
    if settings.auth_mode == 'disabled':
        return {'sub': 'local-development'}

    if not authorization or not authorization.startswith('Bearer '):
        raise AuthenticationError('Authentication required')

    token = authorization[7:].strip()
    if not token:
        raise AuthenticationError('Authentication required')

    decode = decoder or decode_supabase_token
    try:
        claims = decode(token, settings)
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError('Invalid or expired authentication token') from exc

    subject = claims.get('sub')
    if not subject:
        raise AuthenticationError('Invalid or expired authentication token')
    if subject != settings.allowed_user_id:
        raise AuthorizationError('This account is not authorized for this journal')
    return claims


def install_auth_middleware(
    app: FastAPI,
    settings: Settings,
    decoder: Callable[[str, Settings], dict] | None = None,
) -> None:
    @app.middleware('http')
    async def protect_api_request(request: Request, call_next):
        if request.method == 'OPTIONS' or not request.url.path.startswith('/api/'):
            return await call_next(request)

        try:
            claims = authenticate_bearer(
                request.headers.get('authorization'),
                settings,
                decoder=decoder,
            )
        except AuthenticationError as exc:
            return JSONResponse(status_code=401, content={'error': str(exc)})
        except AuthorizationError as exc:
            return JSONResponse(status_code=403, content={'error': str(exc)})

        request.state.auth_claims = claims
        return await call_next(request)

from __future__ import annotations

from functools import lru_cache
from typing import Callable

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from runtime_config import RuntimeConfig, load_runtime_config


class AuthenticationError(Exception):
    """Raised when a bearer token cannot be authenticated."""


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def verify_supabase_access_token(token: str, config: RuntimeConfig) -> dict:
    """Verify a Supabase access JWT without calling the Auth API per request."""
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256"}:
            raise AuthenticationError("Unsupported token algorithm")

        jwks_url = f"{config.supabase_url}/auth/v1/.well-known/jwks.json"
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key

        return jwt.decode(
            token,
            signing_key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=f"{config.supabase_url}/auth/v1",
            options={"require": ["exp", "sub"]},
        )
    except AuthenticationError:
        raise
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid or expired access token") from exc


class SingleUserAuthMiddleware(BaseHTTPMiddleware):
    """Protect all API routes and additionally restrict them to one owner UUID."""

    def __init__(
        self,
        app,
        *,
        config_loader: Callable[[], RuntimeConfig] = load_runtime_config,
        verifier: Callable[[str, RuntimeConfig], dict] = verify_supabase_access_token,
    ):
        super().__init__(app)
        self.config_loader = config_loader
        self.verifier = verifier

    async def dispatch(self, request, call_next):
        config = self.config_loader()
        path = request.url.path

        if (
            config.auth_mode == "disabled"
            or request.method == "OPTIONS"
            or not path.startswith("/api/")
        ):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            claims = self.verifier(token.strip(), config)
        except AuthenticationError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired access token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if claims.get("sub") != config.authorized_user_id:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": "This account is not authorized for this journal"
                },
            )

        request.state.user = claims
        return await call_next(request)

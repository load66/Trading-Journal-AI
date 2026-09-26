import os
from functools import lru_cache

import jwt
from jwt import InvalidTokenError, PyJWKClient, PyJWKClientError


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_required() -> bool:
    return _truthy(os.getenv("AUTH_REQUIRED"))


def validate_auth_config() -> None:
    if not auth_required():
        return

    missing = [
        name
        for name in ("SUPABASE_URL", "AUTHORIZED_USER_ID")
        if not (os.getenv(name) or "").strip()
    ]
    if missing:
        raise RuntimeError(
            "AUTH_REQUIRED=true requires: " + ", ".join(missing)
        )


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def verify_access_token(token: str) -> dict:
    validate_auth_config()

    supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
    issuer = f"{supabase_url}/auth/v1"
    jwks_url = f"{issuer}/.well-known/jwks.json"

    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in {"RS256", "ES256", "EdDSA"}:
            raise AuthError(401, "Invalid access token")

        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
    except AuthError:
        raise
    except (InvalidTokenError, PyJWKClientError, ValueError, TypeError):
        raise AuthError(401, "Invalid or expired access token")


def authorize_header(authorization: str | None) -> dict:
    if not auth_required():
        return {}

    validate_auth_config()

    if not authorization:
        raise AuthError(401, "Authentication required")

    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "Invalid authorization header")

    claims = verify_access_token(token.strip())
    if claims.get("sub") != os.environ["AUTHORIZED_USER_ID"].strip():
        raise AuthError(403, "Access denied")

    return claims

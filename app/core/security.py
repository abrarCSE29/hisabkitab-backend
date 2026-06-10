"""Supabase JWT verification.

Tokens are decoded and verified locally on every request — the backend never
calls Supabase to validate a session. Two signing modes are supported:

- HS256: legacy projects and dev tokens, verified with `SUPABASE_JWT_SECRET`.
- ES256/RS256: projects on Supabase's newer asymmetric JWT signing keys,
  verified against the project's public JWKS endpoint
  (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, cached by PyJWKClient).
"""

import logging
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")


@lru_cache
def get_jwks_client() -> jwt.PyJWKClient:
    base_url = get_settings().supabase_url.rstrip("/")
    return jwt.PyJWKClient(f"{base_url}/auth/v1/.well-known/jwks.json")


class AuthenticatedUser(BaseModel):
    """Identity extracted from a verified Supabase access token.

    `name`/`avatar_url` come from the token's `user_metadata` claim, which
    Supabase populates from the OAuth provider (e.g. Google profile).
    """

    id: str  # Supabase user UUID (JWT `sub` claim)
    email: str | None = None
    role: str | None = None
    name: str | None = None
    avatar_url: str | None = None


def _resolve_verification_key(token: str):
    """Pick the verification key and algorithm based on the token's header."""
    settings = get_settings()
    algorithm = jwt.get_unverified_header(token).get("alg")

    if algorithm == "HS256":
        return settings.supabase_jwt_secret, algorithm

    if algorithm in ASYMMETRIC_ALGORITHMS:
        if not settings.supabase_url:
            logger.warning("Received %s token but SUPABASE_URL is not configured", algorithm)
            raise jwt.InvalidTokenError("Asymmetric verification not configured")
        try:
            return get_jwks_client().get_signing_key_from_jwt(token).key, algorithm
        except jwt.PyJWKClientError as exc:
            logger.warning("JWKS key lookup failed: %s", exc)
            raise jwt.InvalidTokenError("Unknown signing key")

    raise jwt.InvalidTokenError(f"Unsupported signing algorithm {algorithm!r}")


def decode_supabase_jwt(token: str) -> dict:
    """Verify signature, expiry and audience of a Supabase access token."""
    settings = get_settings()
    try:
        key, algorithm = _resolve_verification_key(token)
        return jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=settings.supabase_jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        logger.warning("Rejected expired access token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("Rejected invalid access token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """FastAPI dependency guarding all private endpoints."""
    if credentials is None:
        logger.warning("Rejected request without bearer token: %s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_supabase_jwt(credentials.credentials)
    # Expose the verified identity to the request-logging middleware.
    request.state.user_id = payload["sub"]
    metadata = payload.get("user_metadata") or {}
    return AuthenticatedUser(
        id=payload["sub"],
        email=payload.get("email") or metadata.get("email"),
        role=payload.get("role"),
        name=metadata.get("full_name") or metadata.get("name"),
        avatar_url=metadata.get("avatar_url") or metadata.get("picture"),
    )

"""Supabase JWT verification.

Supabase issues HS256-signed JWTs using the project's JWT secret
(`SUPABASE_JWT_SECRET`). The backend never talks to Supabase to validate a
session — tokens are decoded and verified locally on every request.
"""

import logging

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    """Identity extracted from a verified Supabase access token."""

    id: str  # Supabase user UUID (JWT `sub` claim)
    email: str | None = None
    role: str | None = None


def decode_supabase_jwt(token: str) -> dict:
    """Verify signature, expiry and audience of a Supabase access token."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
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
    return AuthenticatedUser(
        id=payload["sub"],
        email=payload.get("email"),
        role=payload.get("role"),
    )

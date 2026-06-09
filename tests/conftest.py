import os
from datetime import datetime, timedelta, timezone

import jwt
import mongomock
import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "test-jwt-secret-0123456789abcdef0123456789abcdef"
TEST_AUDIENCE = "authenticated"
TEST_USER_ID = "5f4e9c1a-7b2d-4e3f-9a8b-1c2d3e4f5a6b"

# Must be set before the settings cache is populated by app imports.
os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET
os.environ["SUPABASE_JWT_AUDIENCE"] = TEST_AUDIENCE
os.environ["LOG_FILE"] = ""  # don't write server.log during test runs

from app.api.deps import get_db  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

get_settings.cache_clear()


def make_token(
    sub: str = TEST_USER_ID,
    email: str = "user@example.com",
    expires_in: int = 3600,
    audience: str = TEST_AUDIENCE,
    secret: str = TEST_JWT_SECRET,
) -> str:
    """Mint a Supabase-shaped HS256 access token for tests."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "email": email,
        "role": "authenticated",
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_header(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or make_token()}"}


@pytest.fixture
def mock_db():
    return mongomock.MongoClient()["hisabkitab_test"]


@pytest.fixture
def client(monkeypatch, mock_db):
    # Skip the real MongoDB connection during app lifespan in tests.
    monkeypatch.setattr("app.main.connect_to_mongo", lambda: None)
    monkeypatch.setattr("app.main.close_mongo_connection", lambda: None)

    test_app = create_app()
    test_app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(test_app) as test_client:
        yield test_client

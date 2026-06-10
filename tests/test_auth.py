"""FR-1: Authentication & Session Management.

The backend's responsibility is local verification of Supabase-issued JWTs:
valid tokens resolve to a user identity, while missing, expired, tampered or
mis-scoped tokens are rejected with HTTP 401. Both signing modes are covered:
HS256 (legacy secret) and ES256 via the project JWKS (new Supabase projects).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import get_settings
from tests.conftest import TEST_USER_ID, auth_header, make_token


class TestHealthEndpoint:
    def test_health_is_public(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestJwtVerification:
    def test_valid_token_returns_user_identity(self, client):
        response = client.get("/api/v1/auth/me", headers=auth_header())
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == TEST_USER_ID
        assert body["email"] == "user@example.com"
        assert body["role"] == "authenticated"

    def test_google_profile_metadata_extracted(self, client, mock_db):
        token = make_token(
            name="Abrar Hossain",
            avatar_url="https://lh3.googleusercontent.com/a/photo.jpg",
        )
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Abrar Hossain"
        assert body["avatar_url"] == "https://lh3.googleusercontent.com/a/photo.jpg"

        # The profile name is stamped onto vouchers for feed attribution.
        client.post(
            "/api/v1/vouchers",
            json={"type": "expense", "items": [{"amount": 50}]},
            headers=auth_header(token),
        )
        stored = mock_db.vouchers.find_one()
        assert stored["user_name"] == "Abrar Hossain"

        # ...and onto family membership records.
        client.post("/api/v1/family", json={"name": "Amader Songshar"}, headers=auth_header(token))
        member = mock_db.families.find_one()["members"][0]
        assert member["name"] == "Abrar Hossain"

    def test_missing_token_returns_401(self, client):
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_expired_token_returns_401(self, client):
        # Spec test case: "Supabase JWT Expiry Acceptance"
        expired = make_token(expires_in=-60)
        response = client.get("/api/v1/auth/me", headers=auth_header(expired))
        assert response.status_code == 401
        assert response.json()["detail"] == "Token has expired"

    def test_tampered_signature_returns_401(self, client):
        forged = make_token(secret="attacker-guess-0123456789abcdef0123456789abcdef")
        response = client.get("/api/v1/auth/me", headers=auth_header(forged))
        assert response.status_code == 401

    def test_wrong_audience_returns_401(self, client):
        # e.g. a Supabase anon/service token rather than a user session
        token = make_token(audience="service_role")
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 401

    def test_garbage_token_returns_401(self, client):
        response = client.get("/api/v1/auth/me", headers=auth_header("not-a-jwt"))
        assert response.status_code == 401

    def test_non_bearer_scheme_returns_401(self, client):
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Basic {make_token()}"}
        )
        assert response.status_code == 401


class TestAsymmetricJwtVerification:
    """New Supabase projects sign access tokens with ES256 + a public JWKS."""

    @pytest.fixture
    def es256_setup(self, monkeypatch):
        private_key = ec.generate_private_key(ec.SECP256R1())
        stub_client = SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(
                key=private_key.public_key()
            )
        )
        monkeypatch.setattr("app.core.security.get_jwks_client", lambda: stub_client)
        monkeypatch.setattr(get_settings(), "supabase_url", "https://proj.supabase.co")
        return private_key

    def make_es256_token(self, private_key, expires_in: int = 3600, sub: str = TEST_USER_ID):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": sub,
            "email": "google-user@gmail.com",
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        }
        return jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": "key-1"})

    def test_valid_es256_token_accepted(self, client, es256_setup):
        token = self.make_es256_token(es256_setup)
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 200
        assert response.json()["email"] == "google-user@gmail.com"

    def test_expired_es256_token_returns_401(self, client, es256_setup):
        token = self.make_es256_token(es256_setup, expires_in=-60)
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 401
        assert response.json()["detail"] == "Token has expired"

    def test_es256_with_wrong_key_returns_401(self, client, es256_setup):
        other_key = ec.generate_private_key(ec.SECP256R1())
        token = self.make_es256_token(other_key)
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 401

    def test_es256_rejected_when_supabase_url_unset(self, client, es256_setup, monkeypatch):
        monkeypatch.setattr(get_settings(), "supabase_url", "")
        token = self.make_es256_token(es256_setup)
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 401

    def test_unsupported_algorithm_returns_401(self, client):
        token = jwt.encode(
            {"sub": TEST_USER_ID, "aud": "authenticated"}, "x" * 32, algorithm="HS512"
        )
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 401

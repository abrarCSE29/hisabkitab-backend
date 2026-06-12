"""Security hardening: startup secret guard, CORS allowlist, rate limiting,
payload caps, invite hygiene, and JWKS outage handling."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core.config import DEV_PLACEHOLDER_JWT_SECRET, get_settings
from app.main import create_app
from app.services.ocr import get_openai_client
from tests.conftest import TEST_USER_ID, auth_header, make_token


@pytest.fixture
def bare_app(monkeypatch):
    """create_app() without connecting to MongoDB."""
    monkeypatch.setattr("app.main.connect_to_mongo", lambda: None)
    monkeypatch.setattr("app.main.close_mongo_connection", lambda: None)
    return create_app


class TestPlaceholderSecretGuard:
    def test_refuses_to_start_with_placeholder_secret(self, bare_app, monkeypatch):
        monkeypatch.setattr(get_settings(), "supabase_jwt_secret", DEV_PLACEHOLDER_JWT_SECRET)
        with pytest.raises(RuntimeError, match="SUPABASE_JWT_SECRET"):
            bare_app()

    def test_placeholder_allowed_in_explicit_debug_mode(self, bare_app, monkeypatch):
        monkeypatch.setattr(get_settings(), "supabase_jwt_secret", DEV_PLACEHOLDER_JWT_SECRET)
        monkeypatch.setattr(get_settings(), "debug", True)
        assert bare_app() is not None


class TestCorsPolicy:
    PREFLIGHT = {
        "Origin": "https://hisabkitab.vercel.app",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }

    def test_wildcard_dev_default_has_no_credentials(self, client):
        response = client.options("/api/v1/vouchers", headers=self.PREFLIGHT)
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers

    def test_configured_allowlist_pins_origin(self, bare_app, monkeypatch):
        monkeypatch.setattr(
            get_settings(), "cors_origins", "https://hisabkitab.vercel.app,http://localhost:3000"
        )
        with TestClient(bare_app()) as pinned_client:
            response = pinned_client.options("/api/v1/vouchers", headers=self.PREFLIGHT)
            assert (
                response.headers["access-control-allow-origin"]
                == "https://hisabkitab.vercel.app"
            )
            assert response.headers["access-control-allow-credentials"] == "true"

            evil = {**self.PREFLIGHT, "Origin": "https://evil.example.com"}
            response = pinned_client.options("/api/v1/vouchers", headers=evil)
            assert "access-control-allow-origin" not in response.headers


class TestRateLimiting:
    def test_join_attempts_are_capped(self, client):
        for _ in range(10):
            response = client.post(
                "/api/v1/family/join", json={"code": "deadbeef"}, headers=auth_header()
            )
            assert response.status_code == 404  # wrong code, but allowed through

        response = client.post(
            "/api/v1/family/join", json={"code": "deadbeef"}, headers=auth_header()
        )
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_limit_is_per_user(self, client):
        for _ in range(10):
            client.post("/api/v1/family/join", json={"code": "deadbeef"}, headers=auth_header())

        other = make_token(sub="another-user-uuid", email="other@example.com")
        response = client.post(
            "/api/v1/family/join", json={"code": "deadbeef"}, headers=auth_header(other)
        )
        assert response.status_code == 404  # fresh budget, not 429

    def test_ocr_calls_are_capped(self, client):
        # OPENAI_API_KEY is empty in tests, so each allowed call returns 503 —
        # the limiter must still count them and trip on the 21st.
        body = {"image_url": "https://example.com/r.webp"}
        for _ in range(20):
            response = client.post("/api/v1/vouchers/ocr", json=body, headers=auth_header())
            assert response.status_code == 503

        response = client.post("/api/v1/vouchers/ocr", json=body, headers=auth_header())
        assert response.status_code == 429


class TestPayloadCaps:
    def test_rejects_more_than_100_items(self, client):
        payload = {"type": "expense", "items": [{"amount": 1}] * 101}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

    def test_rejects_oversized_item_name(self, client):
        payload = {"type": "expense", "items": [{"name": "x" * 201, "amount": 1}]}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

    def test_rejects_absurd_amount(self, client):
        payload = {"type": "expense", "items": [{"amount": 2_000_000_000}]}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

    def test_hundred_items_still_accepted(self, client):
        payload = {"type": "expense", "items": [{"amount": 1}] * 100}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 201


class TestInviteHygiene:
    def _create_family(self, client):
        response = client.post(
            "/api/v1/family", json={"name": "F"}, headers=auth_header()
        )
        assert response.status_code == 201

    def test_cannot_invite_existing_member(self, client, mock_db):
        self._create_family(client)
        # The creator's own email is already on the member list.
        response = client.post(
            "/api/v1/family/invite", json={"email": "USER@example.com"}, headers=auth_header()
        )
        assert response.status_code == 409

    def test_reinvite_replaces_pending_code(self, client, mock_db):
        self._create_family(client)
        for _ in range(2):
            response = client.post(
                "/api/v1/family/invite",
                json={"email": "spouse@example.com"},
                headers=auth_header(),
            )
            assert response.status_code == 200

        invites = mock_db.families.find_one()["invites"]
        assert len(invites) == 1  # replaced, not stacked

        # Old (first) code was revoked; only the latest works.
        spouse = make_token(sub="spouse-uuid", email="spouse@example.com")
        response = client.post(
            "/api/v1/family/join", json={"code": invites[0]["code"]}, headers=auth_header(spouse)
        )
        assert response.status_code == 200


class TestJwksOutage:
    def test_supabase_unreachable_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "supabase_url", "https://proj.supabase.co")

        class DownJwks:
            def get_signing_key_from_jwt(self, token):
                raise jwt.PyJWKClientConnectionError("connection refused")

        monkeypatch.setattr("app.core.security.get_jwks_client", lambda: DownJwks())

        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {"sub": TEST_USER_ID, "aud": "authenticated", "iat": now,
             "exp": now + timedelta(hours=1)},
            key,
            algorithm="ES256",
            headers={"kid": "k1"},
        )
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 503


class TestOpenAiClientReuse:
    def test_client_is_cached_with_bounded_timeout(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test-cache")
        first = get_openai_client()
        second = get_openai_client()
        assert first is second
        assert first.timeout == 30.0
        assert first.max_retries == 1

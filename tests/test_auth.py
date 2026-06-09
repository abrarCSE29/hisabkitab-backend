"""FR-1: Authentication & Session Management.

The backend's responsibility is local verification of Supabase-issued JWTs:
valid tokens resolve to a user identity, while missing, expired, tampered or
mis-scoped tokens are rejected with HTTP 401.
"""

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

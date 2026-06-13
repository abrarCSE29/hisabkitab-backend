"""User profile sync on sign-in and family-member enrichment."""

from datetime import datetime, timezone

from tests.conftest import auth_header, make_token


class TestAuthMeUpsert:
    def test_me_records_user_on_first_call(self, client, mock_db):
        token = make_token(
            sub="u-1",
            email="rahim@example.com",
            name="Rahim Uddin",
            avatar_url="https://img/rahim.png",
        )
        response = client.get("/api/v1/auth/me", headers=auth_header(token))
        assert response.status_code == 200

        stored = mock_db.users.find_one({"_id": "u-1"})
        assert stored["email"] == "rahim@example.com"
        assert stored["name"] == "Rahim Uddin"
        assert stored["avatar_url"] == "https://img/rahim.png"
        assert "created_at" in stored and "last_seen_at" in stored

    def test_me_is_idempotent_and_refreshes(self, client, mock_db):
        first = make_token(sub="u-2", email="a@x.com", name="Old Name")
        client.get("/api/v1/auth/me", headers=auth_header(first))
        created_at = mock_db.users.find_one({"_id": "u-2"})["created_at"]

        second = make_token(sub="u-2", email="a@x.com", name="New Name")
        client.get("/api/v1/auth/me", headers=auth_header(second))

        docs = list(mock_db.users.find({"_id": "u-2"}))
        assert len(docs) == 1  # upsert, not duplicate
        assert docs[0]["name"] == "New Name"
        assert docs[0]["created_at"] == created_at  # creation time preserved

    def test_refresh_without_metadata_keeps_avatar(self, client, mock_db):
        # A Google sign-in captures the avatar...
        rich = make_token(sub="u-3", email="g@x.com", name="Google User",
                          avatar_url="https://img/g.png")
        client.get("/api/v1/auth/me", headers=auth_header(rich))
        # ...a later refreshed token without user_metadata must not wipe it.
        bare = make_token(sub="u-3", email="g@x.com")
        client.get("/api/v1/auth/me", headers=auth_header(bare))

        stored = mock_db.users.find_one({"_id": "u-3"})
        assert stored["avatar_url"] == "https://img/g.png"
        assert stored["name"] == "Google User"


class TestFamilyMemberEnrichment:
    def test_members_get_avatar_from_users_collection(self, client, mock_db):
        # Admin creates a family (also records their profile).
        admin = make_token(sub="admin-1", email="admin@x.com", name="Admin Apa",
                           avatar_url="https://img/admin.png")
        client.get("/api/v1/auth/me", headers=auth_header(admin))
        created = client.post("/api/v1/family", json={"name": "Songshar"},
                              headers=auth_header(admin))
        assert created.status_code == 201

        families = client.get("/api/v1/family", headers=auth_header(admin)).json()
        member = next(m for m in families[0]["members"] if m["user_id"] == "admin-1")
        assert member["avatar_url"] == "https://img/admin.png"
        assert member["name"] == "Admin Apa"

    def test_enrichment_reflects_updated_profile(self, client, mock_db):
        admin = make_token(sub="admin-2", email="a2@x.com", name="First Name")
        client.post("/api/v1/family", json={"name": "F2"}, headers=auth_header(admin))

        # The member updates their Google display name on a later sign-in.
        renamed = make_token(sub="admin-2", email="a2@x.com", name="Updated Name",
                             avatar_url="https://img/new.png")
        client.get("/api/v1/auth/me", headers=auth_header(renamed))

        families = client.get("/api/v1/family", headers=auth_header(admin)).json()
        member = families[0]["members"][0]
        assert member["name"] == "Updated Name"
        assert member["avatar_url"] == "https://img/new.png"

    def test_member_without_profile_falls_back_to_membership_fields(self, client, mock_db):
        # Seed a family with a member who never synced a profile.
        mock_db.families.insert_one(
            {
                "name": "Legacy",
                "created_by": "ghost",
                "members": [
                    {"user_id": "ghost", "role": "admin", "email": "ghost@x.com",
                     "name": "Ghost"},
                ],
                "invites": [],
                "created_at": datetime.now(timezone.utc),
            }
        )
        token = make_token(sub="ghost", email="ghost@x.com")
        families = client.get("/api/v1/family", headers=auth_header(token)).json()
        member = families[0]["members"][0]
        assert member["name"] == "Ghost"  # fallback to stored membership field
        assert member["email"] == "ghost@x.com"
        assert member["avatar_url"] is None

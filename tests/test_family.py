"""FR-4: Optional Shared Family Space.

Family creation (creator = admin), email invites with join codes, joining,
and the spec integration test "Solo to Family Visibility Transition":
voucher feeds stay scoped to the caller's user_id until an explicit
family_id context is supplied.
"""

from tests.conftest import TEST_USER_ID, auth_header, make_token

MEMBER_UUID = "9e8d7c6b-5a4f-4e3d-2c1b-0a9f8e7d6c5b"
MEMBER_EMAIL = "spouse@example.com"


def member_token(email: str = MEMBER_EMAIL) -> str:
    return make_token(sub=MEMBER_UUID, email=email)


def create_family(client, name: str = "Amader Songshar") -> str:
    response = client.post("/api/v1/family", json={"name": name}, headers=auth_header())
    assert response.status_code == 201
    return response.json()["family_id"]


def invite_and_get_code(client, mock_db, email: str = MEMBER_EMAIL) -> str:
    response = client.post("/api/v1/family/invite", json={"email": email}, headers=auth_header())
    assert response.status_code == 200
    assert response.json() == {"status": "invited"}
    return mock_db.families.find_one()["invites"][-1]["code"]


class TestCreateFamily:
    def test_creator_becomes_admin(self, client, mock_db):
        family_id = create_family(client)
        stored = mock_db.families.find_one()
        assert str(stored["_id"]) == family_id
        assert stored["name"] == "Amader Songshar"
        assert stored["created_by"] == TEST_USER_ID
        assert stored["members"] == [{"user_id": TEST_USER_ID, "role": "admin"}]

    def test_requires_auth(self, client):
        assert client.post("/api/v1/family", json={"name": "X"}).status_code == 401

    def test_rejects_empty_name(self, client):
        response = client.post("/api/v1/family", json={"name": ""}, headers=auth_header())
        assert response.status_code == 422

    def test_list_my_families(self, client):
        create_family(client)
        response = client.get("/api/v1/family", headers=auth_header())
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["name"] == "Amader Songshar"
        assert "invites" not in body[0]  # join codes never leak to clients

    def test_list_excludes_other_peoples_families(self, client):
        create_family(client)
        response = client.get("/api/v1/family", headers=auth_header(member_token()))
        assert response.json() == []


class TestInvite:
    def test_admin_invite_stores_code_and_sends_email(self, client, mock_db, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "app.services.email.send_invite_email",
            lambda to_email, family_name, join_code: sent.append((to_email, join_code)),
        )
        create_family(client)
        code = invite_and_get_code(client, mock_db)

        invite = mock_db.families.find_one()["invites"][0]
        assert invite["email"] == MEMBER_EMAIL
        assert invite["status"] == "pending"
        assert sent == [(MEMBER_EMAIL, code)]

    def test_invite_without_family_returns_404(self, client):
        response = client.post(
            "/api/v1/family/invite", json={"email": MEMBER_EMAIL}, headers=auth_header()
        )
        assert response.status_code == 404

    def test_non_admin_member_cannot_invite(self, client, mock_db):
        create_family(client)
        code = invite_and_get_code(client, mock_db)
        client.post("/api/v1/family/join", json={"code": code}, headers=auth_header(member_token()))

        family_id = str(mock_db.families.find_one()["_id"])
        response = client.post(
            "/api/v1/family/invite",
            json={"email": "third@example.com", "family_id": family_id},
            headers=auth_header(member_token()),
        )
        assert response.status_code == 403

    def test_rejects_invalid_email(self, client):
        create_family(client)
        response = client.post(
            "/api/v1/family/invite", json={"email": "not-an-email"}, headers=auth_header()
        )
        assert response.status_code == 422

    def test_ambiguous_family_requires_family_id(self, client):
        create_family(client, "Family One")
        create_family(client, "Family Two")
        response = client.post(
            "/api/v1/family/invite", json={"email": MEMBER_EMAIL}, headers=auth_header()
        )
        assert response.status_code == 400


class TestJoin:
    def test_valid_code_adds_member(self, client, mock_db):
        family_id = create_family(client)
        code = invite_and_get_code(client, mock_db)

        response = client.post(
            "/api/v1/family/join", json={"code": code}, headers=auth_header(member_token())
        )
        assert response.status_code == 200
        assert response.json() == {"family_id": family_id, "name": "Amader Songshar"}

        stored = mock_db.families.find_one()
        assert {"user_id": MEMBER_UUID, "role": "member"} in stored["members"]
        assert stored["invites"][0]["status"] == "accepted"

    def test_invalid_code_returns_404(self, client):
        response = client.post(
            "/api/v1/family/join", json={"code": "deadbeef"}, headers=auth_header(member_token())
        )
        assert response.status_code == 404

    def test_code_bound_to_invited_email(self, client, mock_db):
        create_family(client)
        code = invite_and_get_code(client, mock_db)
        intruder = make_token(sub="intruder-uuid", email="intruder@example.com")
        response = client.post(
            "/api/v1/family/join", json={"code": code}, headers=auth_header(intruder)
        )
        assert response.status_code == 403

    def test_code_is_single_use(self, client, mock_db):
        create_family(client)
        code = invite_and_get_code(client, mock_db)
        first = client.post(
            "/api/v1/family/join", json={"code": code}, headers=auth_header(member_token())
        )
        assert first.status_code == 200
        second = client.post(
            "/api/v1/family/join", json={"code": code}, headers=auth_header(member_token())
        )
        assert second.status_code == 410


class TestSoloToFamilyVisibilityTransition:
    """Spec integration test: feeds stay user-scoped until family context is set."""

    def _post_voucher(self, client, token, amount, family_id=None):
        payload = {"type": "expense", "items": [{"amount": amount}]}
        if family_id:
            payload["family_id"] = family_id
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header(token))
        assert response.status_code == 201

    def test_family_feed_aggregates_only_family_vouchers(self, client, mock_db):
        admin = make_token()
        member = member_token()

        # Solo era: both users log personal vouchers.
        self._post_voucher(client, admin, 100)
        self._post_voucher(client, member, 200)

        # Family era: admin creates the family, member joins via invite code.
        family_id = create_family(client)
        code = invite_and_get_code(client, mock_db)
        client.post("/api/v1/family/join", json={"code": code}, headers=auth_header(member))

        # Both log shared vouchers under the family context.
        self._post_voucher(client, admin, 300, family_id=family_id)
        self._post_voucher(client, member, 400, family_id=family_id)

        # Solo feeds remain scoped to each user's own records (solo + shared).
        admin_solo = client.get("/api/v1/vouchers", headers=auth_header(admin)).json()
        assert sorted(v["voucher_total"] for v in admin_solo) == [100, 300]

        # Family feed compiles both members' shared vouchers — and nothing solo.
        family_feed = client.get(
            f"/api/v1/vouchers?family_id={family_id}", headers=auth_header(member)
        ).json()
        assert sorted(v["voucher_total"] for v in family_feed) == [300, 400]
        assert {v["user_id"] for v in family_feed} == {TEST_USER_ID, MEMBER_UUID}

    def test_outsider_cannot_read_family_feed(self, client, mock_db):
        family_id = create_family(client)
        outsider = make_token(sub="outsider-uuid", email="outsider@example.com")
        response = client.get(
            f"/api/v1/vouchers?family_id={family_id}", headers=auth_header(outsider)
        )
        assert response.status_code == 403

    def test_member_cannot_post_into_foreign_family(self, client, mock_db):
        family_id = create_family(client)
        outsider = make_token(sub="outsider-uuid", email="outsider@example.com")
        payload = {"type": "expense", "items": [{"amount": 10}], "family_id": family_id}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header(outsider))
        assert response.status_code == 403

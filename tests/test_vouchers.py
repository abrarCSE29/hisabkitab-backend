"""FR-2: Quick & Multi-Item Voucher Logging.

Covers quick single-amount saves, multi-item vouchers, server-side total
summation without float drift (spec unit test "Voucher Mathematical
Summation"), input validation, and user-scoped retrieval (spec integration
test "Solo to Family Visibility Transition", solo side).
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.schemas.voucher import VoucherItem
from app.services.vouchers import compute_voucher_total
from tests.conftest import TEST_USER_ID, auth_header, make_token  # noqa: F401


def quick_expense(amount: float = 250.0, **overrides) -> dict:
    payload = {"type": "expense", "items": [{"amount": amount}]}
    payload.update(overrides)
    return payload


class TestVoucherTotalSummation:
    def test_sums_item_amounts_exactly(self):
        items = [VoucherItem(name="a", amount=10.10), VoucherItem(name="b", amount=20.25)]
        assert compute_voucher_total(items) == 30.35

    def test_no_float_precision_drift(self):
        # 0.1 + 0.2 must be exactly 0.3, not 0.30000000000000004
        items = [VoucherItem(amount=0.1), VoucherItem(amount=0.2)]
        assert compute_voucher_total(items) == 0.3

    def test_single_item_total(self):
        assert compute_voucher_total([VoucherItem(amount=99.99)]) == 99.99


class TestCreateVoucher:
    def test_quick_save_amount_only(self, client, mock_db):
        response = client.post("/api/v1/vouchers", json=quick_expense(120), headers=auth_header())
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "success"

        stored = mock_db.vouchers.find_one()
        assert str(stored["_id"]) == body["id"]
        assert stored["user_id"] == TEST_USER_ID
        assert stored["user_email"] == "user@example.com"  # display identity for feeds
        assert stored["voucher_total"] == 120
        assert stored["family_id"] is None

    def test_multi_item_voucher_calculates_total(self, client, mock_db):
        payload = {
            "type": "expense",
            "category_id": "bazaar",
            "items": [
                {"name": "Rice 5kg", "amount": 400.50},
                {"name": "Lentils", "amount": 130.25},
                {"name": "Fish", "amount": 650.00},
            ],
        }
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 201

        stored = mock_db.vouchers.find_one()
        assert stored["voucher_total"] == 1180.75
        assert len(stored["items"]) == 3
        assert stored["category_id"] == "bazaar"

    def test_income_voucher(self, client, mock_db):
        response = client.post(
            "/api/v1/vouchers", json=quick_expense(50000, type="income"), headers=auth_header()
        )
        assert response.status_code == 201
        assert mock_db.vouchers.find_one()["type"] == "income"

    def test_requires_auth(self, client):
        assert client.post("/api/v1/vouchers", json=quick_expense()).status_code == 401

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "transfer", "items": [{"amount": 10}]},  # invalid type
            {"type": "expense", "items": []},  # no items
            {"type": "expense", "items": [{"amount": -5}]},  # negative amount
            {"type": "expense", "items": [{"amount": 0}]},  # zero amount
            {"type": "expense"},  # items missing
        ],
    )
    def test_rejects_invalid_payloads(self, client, payload):
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

    def test_rejects_malformed_family_id(self, client):
        response = client.post(
            "/api/v1/vouchers", json=quick_expense(family_id="not-an-objectid"),
            headers=auth_header(),
        )
        assert response.status_code == 422

    def test_rejects_family_id_of_non_member(self, client):
        response = client.post(
            "/api/v1/vouchers", json=quick_expense(family_id="65cb7f0000000000000000aa"),
            headers=auth_header(),
        )
        assert response.status_code == 403


class TestListVouchers:
    def seed(self, mock_db, user_id: str, amount: float, days_ago: int = 0) -> None:
        mock_db.vouchers.insert_one(
            {
                "family_id": None,
                "user_id": user_id,
                "type": "expense",
                "category_id": "bazaar",
                "items": [{"name": "x", "amount": amount}],
                "voucher_total": amount,
                "image_url": None,
                "created_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
            }
        )

    def test_returns_only_own_vouchers(self, client, mock_db):
        self.seed(mock_db, TEST_USER_ID, 100)
        self.seed(mock_db, "someone-else-uuid", 999)

        response = client.get("/api/v1/vouchers", headers=auth_header())
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["voucher_total"] == 100
        assert body[0]["user_id"] == TEST_USER_ID

    def test_personal_feed_excludes_own_family_vouchers(self, client, mock_db):
        # A family entry the user created must NOT leak into their personal feed.
        self.seed(mock_db, TEST_USER_ID, 100)  # personal entry
        mock_db.vouchers.insert_one(
            {
                "family_id": ObjectId(),
                "user_id": TEST_USER_ID,
                "type": "expense",
                "category_id": "bazaar",
                "items": [{"name": "x", "amount": 500}],
                "voucher_total": 500,
                "image_url": None,
                "created_at": datetime.now(timezone.utc),
            }
        )

        response = client.get("/api/v1/vouchers", headers=auth_header())
        body = response.json()
        assert len(body) == 1
        assert body[0]["voucher_total"] == 100  # only the personal one

    def test_reverse_chronological_order_and_limit(self, client, mock_db):
        for days_ago in (2, 0, 1):  # inserted out of order on purpose
            self.seed(mock_db, TEST_USER_ID, amount=100 + days_ago, days_ago=days_ago)

        response = client.get("/api/v1/vouchers?limit=2", headers=auth_header())
        body = response.json()
        assert len(body) == 2
        assert [v["voucher_total"] for v in body] == [100, 101]  # newest first

    def test_empty_feed_for_new_user(self, client, mock_db):
        self.seed(mock_db, "someone-else-uuid", 999)
        token = make_token(sub="brand-new-user-uuid")
        response = client.get("/api/v1/vouchers", headers=auth_header(token))
        assert response.status_code == 200
        assert response.json() == []

    def test_requires_auth(self, client):
        assert client.get("/api/v1/vouchers").status_code == 401

    def test_rejects_limit_out_of_bounds(self, client):
        assert client.get("/api/v1/vouchers?limit=0", headers=auth_header()).status_code == 422
        assert client.get("/api/v1/vouchers?limit=500", headers=auth_header()).status_code == 422


def create_and_get_id(client, payload: dict | None = None, token: str | None = None) -> str:
    response = client.post(
        "/api/v1/vouchers", json=payload or quick_expense(), headers=auth_header(token)
    )
    assert response.status_code == 201
    return response.json()["id"]


class TestGetVoucher:
    def test_owner_can_fetch_own_voucher(self, client):
        payload = {
            "type": "expense",
            "category_id": "bazaar",
            "items": [{"name": "Rice", "amount": 400.5}],
        }
        voucher_id = create_and_get_id(client, payload)

        response = client.get(f"/api/v1/vouchers/{voucher_id}", headers=auth_header())
        assert response.status_code == 200
        body = response.json()
        assert body["_id"] == voucher_id
        assert body["category_id"] == "bazaar"
        assert body["items"] == [{"name": "Rice", "amount": 400.5}]
        assert body["updated_at"] is None

    def test_solo_voucher_hidden_from_other_users(self, client):
        voucher_id = create_and_get_id(client)
        stranger = make_token(sub="stranger-uuid", email="stranger@example.com")
        response = client.get(f"/api/v1/vouchers/{voucher_id}", headers=auth_header(stranger))
        assert response.status_code == 404

    def test_family_member_can_view_family_voucher(self, client, mock_db):
        family_id = str(
            mock_db.families.insert_one(
                {
                    "name": "F",
                    "created_by": TEST_USER_ID,
                    "members": [
                        {"user_id": TEST_USER_ID, "role": "admin", "email": None, "name": None},
                        {"user_id": "member-uuid", "role": "member", "email": None, "name": None},
                    ],
                    "invites": [],
                    "created_at": datetime.now(timezone.utc),
                }
            ).inserted_id
        )
        voucher_id = create_and_get_id(client, quick_expense(300, family_id=family_id))

        member = make_token(sub="member-uuid", email="member@example.com")
        response = client.get(f"/api/v1/vouchers/{voucher_id}", headers=auth_header(member))
        assert response.status_code == 200
        assert response.json()["voucher_total"] == 300

    def test_unknown_id_returns_404_and_malformed_422(self, client):
        ok_format = "65cb7f0000000000000000aa"
        assert (
            client.get(f"/api/v1/vouchers/{ok_format}", headers=auth_header()).status_code == 404
        )
        assert client.get("/api/v1/vouchers/not-an-id", headers=auth_header()).status_code == 422

    def test_requires_auth(self, client):
        assert client.get("/api/v1/vouchers/65cb7f0000000000000000aa").status_code == 401


class TestUpdateVoucher:
    def update(self, client, voucher_id, payload, token=None):
        return client.put(
            f"/api/v1/vouchers/{voucher_id}", json=payload, headers=auth_header(token)
        )

    def test_owner_updates_items_and_total_recomputed(self, client, mock_db):
        voucher_id = create_and_get_id(client)
        response = self.update(
            client,
            voucher_id,
            {
                "type": "expense",
                "category_id": "bazaar",
                "items": [{"name": "Rice", "amount": 0.1}, {"name": "Salt", "amount": 0.2}],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["voucher_total"] == 0.3  # Decimal-exact recomputation
        assert body["category_id"] == "bazaar"
        assert body["updated_at"] is not None

        stored = mock_db.vouchers.find_one()
        assert stored["voucher_total"] == 0.3
        assert stored["created_at"] is not None  # immutable fields preserved
        assert stored["user_id"] == TEST_USER_ID

    def test_update_validates_category_against_type(self, client):
        voucher_id = create_and_get_id(client)
        response = self.update(
            client,
            voucher_id,
            {"type": "expense", "category_id": "salary", "items": [{"amount": 10}]},
        )
        assert response.status_code == 422

    def test_family_member_cannot_edit_others_voucher(self, client, mock_db):
        family_id = str(
            mock_db.families.insert_one(
                {
                    "name": "F",
                    "created_by": TEST_USER_ID,
                    "members": [
                        {"user_id": TEST_USER_ID, "role": "admin", "email": None, "name": None},
                        {"user_id": "member-uuid", "role": "member", "email": None, "name": None},
                    ],
                    "invites": [],
                    "created_at": datetime.now(timezone.utc),
                }
            ).inserted_id
        )
        voucher_id = create_and_get_id(client, quick_expense(300, family_id=family_id))

        member = make_token(sub="member-uuid", email="member@example.com")
        response = self.update(
            client, voucher_id, {"type": "expense", "items": [{"amount": 1}]}, token=member
        )
        assert response.status_code == 403  # visible to them, but not editable

    def test_stranger_gets_404(self, client):
        voucher_id = create_and_get_id(client)
        stranger = make_token(sub="stranger-uuid", email="s@example.com")
        response = self.update(
            client, voucher_id, {"type": "expense", "items": [{"amount": 1}]}, token=stranger
        )
        assert response.status_code == 404

    def test_update_rejects_invalid_image_url(self, client):
        voucher_id = create_and_get_id(client)
        response = self.update(
            client,
            voucher_id,
            {
                "type": "expense",
                "items": [{"amount": 1}],
                "image_url": "data:image/png;base64,AAAA",
            },
        )
        assert response.status_code == 422

    def test_family_id_is_immutable(self, client, mock_db):
        voucher_id = create_and_get_id(client)  # solo voucher
        response = self.update(
            client,
            voucher_id,
            {
                "type": "expense",
                "items": [{"amount": 5}],
                "family_id": "65cb7f0000000000000000aa",  # ignored by VoucherUpdate
            },
        )
        assert response.status_code == 200
        assert mock_db.vouchers.find_one()["family_id"] is None


class TestDeleteVoucher:
    def delete(self, client, voucher_id, token=None):
        return client.delete(f"/api/v1/vouchers/{voucher_id}", headers=auth_header(token))

    def test_owner_can_delete_own_voucher(self, client, mock_db):
        voucher_id = create_and_get_id(client)
        response = self.delete(client, voucher_id)
        assert response.status_code == 204
        assert mock_db.vouchers.find_one() is None

    def test_family_member_cannot_delete_others_voucher(self, client, mock_db):
        family_id = str(
            mock_db.families.insert_one(
                {
                    "name": "F",
                    "created_by": TEST_USER_ID,
                    "members": [
                        {"user_id": TEST_USER_ID, "role": "admin", "email": None, "name": None},
                        {"user_id": "member-uuid", "role": "member", "email": None, "name": None},
                    ],
                    "invites": [],
                    "created_at": datetime.now(timezone.utc),
                }
            ).inserted_id
        )
        voucher_id = create_and_get_id(client, quick_expense(300, family_id=family_id))

        member = make_token(sub="member-uuid", email="member@example.com")
        response = self.delete(client, voucher_id, token=member)
        assert response.status_code == 403  # visible to them, but not deletable
        assert mock_db.vouchers.find_one() is not None  # still there

    def test_stranger_gets_404(self, client, mock_db):
        voucher_id = create_and_get_id(client)
        stranger = make_token(sub="stranger-uuid", email="s@example.com")
        response = self.delete(client, voucher_id, token=stranger)
        assert response.status_code == 404
        assert mock_db.vouchers.find_one() is not None  # untouched

    def test_unknown_id_404_and_malformed_422(self, client):
        assert self.delete(client, "65cb7f0000000000000000aa").status_code == 404
        assert self.delete(client, "not-an-id").status_code == 422

    def test_requires_auth(self, client):
        assert client.delete("/api/v1/vouchers/65cb7f0000000000000000aa").status_code == 401

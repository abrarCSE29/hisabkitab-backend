"""FR-2: Quick & Multi-Item Voucher Logging.

Covers quick single-amount saves, multi-item vouchers, server-side total
summation without float drift (spec unit test "Voucher Mathematical
Summation"), input validation, and user-scoped retrieval (spec integration
test "Solo to Family Visibility Transition", solo side).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.voucher import VoucherItem
from app.services.vouchers import compute_voucher_total
from tests.conftest import TEST_USER_ID, auth_header, make_token


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

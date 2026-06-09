"""FR-3: Localized Expense Categorization.

Categories carry both English and Bangla names; every voucher is tagged with
exactly one category valid for its type, defaulting to the catch-all when the
quick-save flow omits a choice.
"""

import pytest

from app.core.categories import CATEGORIES, get_category, resolve_category_id
from tests.conftest import auth_header


class TestCategoryRegistry:
    def test_registry_has_unique_ids(self):
        ids = [category.id for category in CATEGORIES]
        assert len(ids) == len(set(ids))

    def test_blueprint_examples_are_localized(self):
        bazaar = get_category("bazaar")
        assert bazaar.name_bn == "বাজার"
        assert bazaar.label == "Bazaar (বাজার)"

        dining = get_category("dining")
        assert dining.name_bn == "খাওয়া-দাওয়া"
        assert dining.type == "expense"

    def test_resolve_defaults_when_omitted(self):
        assert resolve_category_id(None, "expense") == "others"
        assert resolve_category_id(None, "income") == "other_income"

    def test_resolve_rejects_unknown_category(self):
        with pytest.raises(ValueError, match="Unknown category_id"):
            resolve_category_id("crypto", "expense")

    def test_resolve_rejects_type_mismatch(self):
        with pytest.raises(ValueError, match="income category"):
            resolve_category_id("salary", "expense")


class TestCategoriesEndpoint:
    def test_requires_auth(self, client):
        assert client.get("/api/v1/categories").status_code == 401

    def test_lists_bilingual_categories(self, client):
        response = client.get("/api/v1/categories", headers=auth_header())
        assert response.status_code == 200
        body = response.json()
        assert len(body) == len(CATEGORIES)
        bazaar = next(category for category in body if category["id"] == "bazaar")
        assert bazaar == {
            "id": "bazaar",
            "name_en": "Bazaar",
            "name_bn": "বাজার",
            "type": "expense",
            "label": "Bazaar (বাজার)",
        }

    def test_filters_by_type(self, client):
        response = client.get("/api/v1/categories?type=income", headers=auth_header())
        body = response.json()
        assert body and all(category["type"] == "income" for category in body)

    def test_rejects_invalid_type_filter(self, client):
        response = client.get("/api/v1/categories?type=savings", headers=auth_header())
        assert response.status_code == 422


class TestVoucherCategoryTagging:
    def test_voucher_with_valid_category(self, client, mock_db):
        payload = {"type": "expense", "category_id": "transport", "items": [{"amount": 30}]}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 201
        assert mock_db.vouchers.find_one()["category_id"] == "transport"

    def test_quick_save_defaults_to_catch_all(self, client, mock_db):
        payload = {"type": "expense", "items": [{"amount": 30}]}
        client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert mock_db.vouchers.find_one()["category_id"] == "others"

    def test_income_quick_save_defaults_to_other_income(self, client, mock_db):
        payload = {"type": "income", "items": [{"amount": 5000}]}
        client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert mock_db.vouchers.find_one()["category_id"] == "other_income"

    def test_rejects_unknown_category(self, client):
        payload = {"type": "expense", "category_id": "crypto", "items": [{"amount": 30}]}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

    def test_rejects_income_category_on_expense_voucher(self, client):
        payload = {"type": "expense", "category_id": "salary", "items": [{"amount": 30}]}
        response = client.post("/api/v1/vouchers", json=payload, headers=auth_header())
        assert response.status_code == 422

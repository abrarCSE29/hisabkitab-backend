"""FR-3: Localized category registry.

Categories are a fixed, code-defined list (no DB collection needed) with both
English and Bangla names. Every voucher is tagged with exactly one category;
quick-save vouchers without an explicit choice fall back to the catch-all
category for their type.
"""

from typing import Literal

from pydantic import BaseModel, computed_field

CategoryType = Literal["income", "expense"]


class Category(BaseModel):
    id: str
    name_en: str
    name_bn: str
    type: CategoryType

    @computed_field
    @property
    def label(self) -> str:
        """Display form used across the UI, e.g. 'Bazaar (বাজার)'."""
        return f"{self.name_en} ({self.name_bn})"


CATEGORIES: list[Category] = [
    # Expense
    Category(id="bazaar", name_en="Bazaar", name_bn="বাজার", type="expense"),
    Category(id="dining", name_en="Dining & Snacks", name_bn="খাওয়া-দাওয়া", type="expense"),
    Category(id="transport", name_en="Transport", name_bn="যাতায়াত", type="expense"),
    Category(id="rent", name_en="House Rent", name_bn="বাসা ভাড়া", type="expense"),
    Category(id="utilities", name_en="Bills & Utilities", name_bn="বিল ও ইউটিলিটি", type="expense"),
    Category(id="health", name_en="Health & Medicine", name_bn="চিকিৎসা", type="expense"),
    Category(id="education", name_en="Education", name_bn="শিক্ষা", type="expense"),
    Category(id="shopping", name_en="Shopping", name_bn="কেনাকাটা", type="expense"),
    Category(id="entertainment", name_en="Entertainment", name_bn="বিনোদন", type="expense"),
    Category(id="others", name_en="Others", name_bn="অন্যান্য", type="expense"),
    # Income
    Category(id="salary", name_en="Salary", name_bn="বেতন", type="income"),
    Category(id="business", name_en="Business", name_bn="ব্যবসা", type="income"),
    Category(id="gift", name_en="Gift & Remittance", name_bn="উপহার ও রেমিট্যান্স", type="income"),
    Category(id="other_income", name_en="Other Income", name_bn="অন্যান্য আয়", type="income"),
]

_BY_ID: dict[str, Category] = {category.id: category for category in CATEGORIES}

DEFAULT_CATEGORY_ID: dict[CategoryType, str] = {"expense": "others", "income": "other_income"}


def get_category(category_id: str) -> Category | None:
    return _BY_ID.get(category_id)


def resolve_category_id(category_id: str | None, voucher_type: CategoryType) -> str:
    """Validate a chosen category against the voucher type, or apply the default.

    Raises ValueError so Pydantic surfaces it as a standard 422 validation error.
    """
    if category_id is None:
        return DEFAULT_CATEGORY_ID[voucher_type]

    category = get_category(category_id)
    if category is None:
        raise ValueError(f"Unknown category_id '{category_id}'")
    if category.type != voucher_type:
        raise ValueError(
            f"Category '{category_id}' is a {category.type} category and "
            f"cannot be used on a {voucher_type} voucher"
        )
    return category.id

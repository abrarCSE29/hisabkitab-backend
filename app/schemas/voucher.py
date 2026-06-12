from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.categories import resolve_category_id
from app.core.config import get_settings
from app.core.storage import validate_image_url


class VoucherItem(BaseModel):
    """One itemized row inside a voucher.

    For quick logging (FR-2: tap +, enter amount, save) the frontend sends a
    single item and may omit the name.
    """

    name: str = Field(default="", max_length=200)
    amount: float = Field(gt=0, le=1_000_000_000)  # sanity cap: 100 crore BDT


class VoucherPayload(BaseModel):
    """Shared editable fields + validation for create and update."""

    type: Literal["income", "expense"]
    category_id: str | None = Field(default=None, max_length=50)
    items: list[VoucherItem] = Field(min_length=1, max_length=100)
    image_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def tag_with_category(self) -> "VoucherPayload":
        """FR-3: every voucher carries exactly one valid category for its type."""
        self.category_id = resolve_category_id(self.category_id, self.type)
        return self

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, value: str | None) -> str | None:
        """FR-5: receipt URLs must point at our Supabase Storage bucket."""
        if value is None:
            return None
        return validate_image_url(value, get_settings().supabase_url)


class VoucherCreate(VoucherPayload):
    family_id: str | None = None  # ObjectId string, only in Family Mode


class VoucherUpdate(VoucherPayload):
    """Full replacement of the editable fields; the workspace (family_id),
    creator identity and created_at are immutable."""


class VoucherCreated(BaseModel):
    status: Literal["success"] = "success"
    id: str


class VoucherOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    family_id: str | None = None
    user_id: str
    user_email: str | None = None
    user_name: str | None = None
    type: str
    category_id: str | None = None
    items: list[VoucherItem]
    voucher_total: float
    image_url: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

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

    name: str = ""
    amount: float = Field(gt=0)


class VoucherCreate(BaseModel):
    type: Literal["income", "expense"]
    category_id: str | None = None
    items: list[VoucherItem] = Field(min_length=1)
    image_url: str | None = None
    family_id: str | None = None  # ObjectId string, only in Family Mode

    @model_validator(mode="after")
    def tag_with_category(self) -> "VoucherCreate":
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


class VoucherCreated(BaseModel):
    status: Literal["success"] = "success"
    id: str


class VoucherOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    family_id: str | None = None
    user_id: str
    type: str
    category_id: str | None = None
    items: list[VoucherItem]
    voucher_total: float
    image_url: str | None = None
    created_at: datetime

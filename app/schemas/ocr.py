from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.core.storage import validate_image_url


class OcrRequest(BaseModel):
    image_url: str = Field(max_length=2048)

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, value: str) -> str:
        # Same FR-5 rule as vouchers: only our Supabase bucket once configured.
        return validate_image_url(value, get_settings().supabase_url)


class ExtractedItem(BaseModel):
    name: str
    amount: float


class ReceiptExtraction(BaseModel):
    """Structured-output schema sent to OpenAI and returned to the client."""

    items: list[ExtractedItem] = Field(default_factory=list)


class OcrFeedback(BaseModel):
    """User's thumbs up/down on how accurate an OCR auto-fill was."""

    rating: Literal["up", "down"]
    image_url: str | None = Field(default=None, max_length=2048)
    item_count: int = Field(default=0, ge=0)

    @field_validator("image_url")
    @classmethod
    def check_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_image_url(value, get_settings().supabase_url)

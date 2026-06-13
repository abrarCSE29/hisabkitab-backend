"""FR-6: Receipt OCR via Groq's vision models (structured outputs).

Groq exposes an OpenAI-compatible API, so the OpenAI SDK is used as the
client with Groq's base URL. The model is configured via OCR_MODEL in .env.

Free-tier resilience (spec edge case "Zero-Cost OpenAI Rate Limits"): rate
limits, exhausted quota, and provider outages map to structured HTTP errors
the frontend can show — never an unhandled 500.
"""

from datetime import datetime, timezone
from functools import lru_cache

from fastapi import HTTPException, status
from openai import APIError, APIStatusError, AuthenticationError, OpenAI, RateLimitError
from pymongo.database import Database

from app.core.config import get_settings
from app.core.security import AuthenticatedUser
from app.schemas.ocr import OcrFeedback, ReceiptExtraction

SYSTEM_PROMPT = (
    "You extract line items from photos of shop receipts from Bangladesh. "
    "Receipts may be printed or handwritten, in Bangla or English. "
    "Return every purchasable line item with its TOTAL amount in BDT for that "
    "line: when a line shows quantity and unit price, multiply them (e.g. "
    "'2 x 80' -> amount 160); when a printed line total exists, use it. Never "
    "return the unit price for multi-quantity lines. Also include VAT, tax, "
    "service charge, delivery fee and any other extra charges, each as its own "
    "item (e.g. name 'VAT', amount 15). Convert Bangla numerals to standard "
    "digits. Exclude only subtotal and grand-total lines, and discount lines "
    "that merely restate the total."
)


@lru_cache(maxsize=2)
def _client_for(api_key: str, base_url: str) -> OpenAI:
    # Bounded timeout/retries: each OCR call occupies a threadpool worker,
    # so a hung provider must not pin threads for minutes.
    return OpenAI(api_key=api_key, base_url=base_url, timeout=30.0, max_retries=1)


def get_ocr_client() -> OpenAI:
    settings = get_settings()
    return _client_for(settings.groq_api_key, settings.groq_base_url)


def extract_receipt_items(image_url: str) -> ReceiptExtraction:
    settings = get_settings()
    if not settings.groq_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR is not configured on this server (missing GROQ_API_KEY)",
        )

    try:
        completion = get_ocr_client().chat.completions.parse(
            model=settings.ocr_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the items from this receipt."},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            response_format=ReceiptExtraction,
        )
    except RateLimitError:
        # Covers both 429 rate limiting and insufficient_quota (payment) errors.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "OCR is temporarily unavailable (provider rate limit or quota). "
            "Please add the items manually or retry later.",
        )
    except AuthenticationError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "OCR provider rejected the server credentials"
        )
    except (APIStatusError, APIError):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "OCR provider returned an unexpected error"
        )

    message = completion.choices[0].message
    parsed: ReceiptExtraction | None = getattr(message, "parsed", None)
    if parsed is None:
        # Model refused or produced no structured payload.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Could not read any items from this image — try a clearer photo",
        )

    # Drop noise rows so results are directly usable as voucher items (amount > 0).
    parsed.items = [item for item in parsed.items if item.amount > 0 and item.name.strip()]
    return parsed


def record_feedback(db: Database, user: AuthenticatedUser, feedback: OcrFeedback) -> None:
    """Persist a user's thumbs up/down on an OCR auto-fill for quality tracking."""
    db.ocr_feedback.insert_one(
        {
            "user_id": user.id,
            "rating": feedback.rating,
            "image_url": feedback.image_url,
            "item_count": feedback.item_count,
            "created_at": datetime.now(timezone.utc),
        }
    )

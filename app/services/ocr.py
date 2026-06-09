"""FR-6: Receipt OCR via the OpenAI Vision API (gpt-4o-mini, structured outputs).

Free-tier resilience (spec edge case "Zero-Cost OpenAI Rate Limits"): rate
limits, exhausted quota, and provider outages map to structured HTTP errors
the frontend can show — never an unhandled 500.
"""

from fastapi import HTTPException, status
from openai import APIError, APIStatusError, AuthenticationError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.schemas.ocr import ReceiptExtraction

OCR_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = (
    "You extract line items from photos of shop receipts from Bangladesh. "
    "Receipts may be printed or handwritten, in Bangla or English. "
    "Return every purchasable line item with its price in BDT. Convert Bangla "
    "numerals to standard digits. Exclude subtotals, VAT lines and grand totals."
)


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=get_settings().openai_api_key)


def extract_receipt_items(image_url: str) -> ReceiptExtraction:
    if not get_settings().openai_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OCR is not configured on this server (missing OpenAI API key)",
        )

    try:
        completion = get_openai_client().chat.completions.parse(
            model=OCR_MODEL,
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

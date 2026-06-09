"""FR-6: Intelligent OCR Parsing.

The OpenAI client is mocked throughout (spec: "Mock handlers intercept
outbound calls to OpenAI"). Covers the happy path plus the spec edge case
"Zero-Cost OpenAI Rate Limits": rate-limit/quota and provider failures must
surface as structured HTTP errors, never unhandled application errors.
"""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, AuthenticationError, RateLimitError

from app.core.config import get_settings
from app.schemas.ocr import ExtractedItem, ReceiptExtraction
from tests.conftest import auth_header

IMAGE_URL = "https://example.com/receipts/bazaar.webp"


def openai_error(error_class, status_code: int):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return error_class("provider error", response=response, body=None)


class FakeOpenAI:
    """Stands in for the OpenAI client: returns a parsed result or raises."""

    def __init__(self, parsed=None, error=None):
        self.calls = []
        completions = SimpleNamespace(parse=self._parse)
        self.chat = SimpleNamespace(completions=completions)
        self._parsed = parsed
        self._error = error

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        message = SimpleNamespace(parsed=self._parsed)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def openai_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test")


def use_fake_client(monkeypatch, fake: FakeOpenAI) -> None:
    monkeypatch.setattr("app.services.ocr.get_openai_client", lambda: fake)


class TestOcrEndpoint:
    def test_requires_auth(self, client):
        response = client.post("/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL})
        assert response.status_code == 401

    def test_rejects_malformed_image_url(self, client, openai_configured):
        response = client.post(
            "/api/v1/vouchers/ocr",
            json={"image_url": "data:image/png;base64,AAAA"},
            headers=auth_header(),
        )
        assert response.status_code == 422

    def test_unconfigured_server_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "openai_api_key", "")
        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 503

    def test_returns_structured_items(self, client, monkeypatch, openai_configured):
        fake = FakeOpenAI(
            parsed=ReceiptExtraction(
                items=[
                    ExtractedItem(name="Rice 5kg", amount=400.5),
                    ExtractedItem(name="Daal", amount=130.0),
                ]
            )
        )
        use_fake_client(monkeypatch, fake)

        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 200
        assert response.json() == {
            "items": [
                {"name": "Rice 5kg", "amount": 400.5},
                {"name": "Daal", "amount": 130.0},
            ]
        }

        # The receipt image was forwarded to the vision model.
        sent = fake.calls[0]
        assert sent["response_format"] is ReceiptExtraction
        assert any(
            part.get("image_url", {}).get("url") == IMAGE_URL
            for part in sent["messages"][1]["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        )

    def test_filters_noise_rows(self, client, monkeypatch, openai_configured):
        fake = FakeOpenAI(
            parsed=ReceiptExtraction(
                items=[
                    ExtractedItem(name="Fish", amount=650.0),
                    ExtractedItem(name="", amount=10.0),  # nameless noise
                    ExtractedItem(name="Discount", amount=-50.0),  # negative row
                    ExtractedItem(name="Bag", amount=0.0),  # zero row
                ]
            )
        )
        use_fake_client(monkeypatch, fake)

        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.json()["items"] == [{"name": "Fish", "amount": 650.0}]

    def test_rate_limit_returns_429_with_guidance(self, client, monkeypatch, openai_configured):
        use_fake_client(monkeypatch, FakeOpenAI(error=openai_error(RateLimitError, 429)))
        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 429
        assert "manually" in response.json()["detail"]

    def test_bad_credentials_return_503(self, client, monkeypatch, openai_configured):
        use_fake_client(monkeypatch, FakeOpenAI(error=openai_error(AuthenticationError, 401)))
        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 503

    def test_provider_outage_returns_502(self, client, monkeypatch, openai_configured):
        use_fake_client(monkeypatch, FakeOpenAI(error=openai_error(APIStatusError, 500)))
        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 502

    def test_model_refusal_returns_422(self, client, monkeypatch, openai_configured):
        use_fake_client(monkeypatch, FakeOpenAI(parsed=None))
        response = client.post(
            "/api/v1/vouchers/ocr", json={"image_url": IMAGE_URL}, headers=auth_header()
        )
        assert response.status_code == 422
        assert "clearer photo" in response.json()["detail"]

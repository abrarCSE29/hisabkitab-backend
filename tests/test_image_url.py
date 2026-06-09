"""FR-5: Image Upload (backend slice).

Compression and the Supabase Storage upload happen client-side; the backend
persists the resulting public URL on the voucher and validates it: well-formed
http(s), and confined to the project's storage bucket once SUPABASE_URL is
configured.
"""

import pytest

from app.core.config import get_settings
from app.core.storage import validate_image_url
from tests.conftest import auth_header

SUPABASE_URL = "https://myproject.supabase.co"
BUCKET_URL = f"{SUPABASE_URL}/storage/v1/object/public/receipts/2026/06/rcpt-001.webp"


def voucher_payload(image_url: str | None) -> dict:
    return {"type": "expense", "items": [{"amount": 99}], "image_url": image_url}


@pytest.fixture
def supabase_configured(monkeypatch):
    # get_settings() is lru_cached, so patch the live instance.
    monkeypatch.setattr(get_settings(), "supabase_url", SUPABASE_URL)


class TestValidateImageUrl:
    def test_accepts_any_https_url_without_supabase_configured(self):
        url = "https://example.com/receipt.jpg"
        assert validate_image_url(url, supabase_url="") == url

    @pytest.mark.parametrize(
        "bad_url",
        [
            "not a url",
            "javascript:alert(1)",
            "data:image/png;base64,AAAA",
            "ftp://example.com/receipt.jpg",
            "https://",
        ],
    )
    def test_rejects_non_http_urls(self, bad_url):
        with pytest.raises(ValueError, match="http"):
            validate_image_url(bad_url, supabase_url="")

    def test_accepts_own_bucket_url(self):
        assert validate_image_url(BUCKET_URL, supabase_url=SUPABASE_URL) == BUCKET_URL

    def test_rejects_foreign_host_when_supabase_configured(self):
        with pytest.raises(ValueError, match="Supabase Storage"):
            validate_image_url("https://evil.example.com/receipt.jpg", supabase_url=SUPABASE_URL)

    def test_rejects_non_public_storage_path(self):
        # Signed/private object paths are not public URLs the dashboard can render.
        with pytest.raises(ValueError, match="Supabase Storage"):
            validate_image_url(
                f"{SUPABASE_URL}/storage/v1/object/sign/receipts/x.webp",
                supabase_url=SUPABASE_URL,
            )

    def test_trailing_slash_on_base_is_normalized(self):
        assert validate_image_url(BUCKET_URL, supabase_url=SUPABASE_URL + "/") == BUCKET_URL


class TestVoucherImageUrl:
    def test_voucher_stores_bucket_image_url(self, client, mock_db, supabase_configured):
        response = client.post(
            "/api/v1/vouchers", json=voucher_payload(BUCKET_URL), headers=auth_header()
        )
        assert response.status_code == 201
        assert mock_db.vouchers.find_one()["image_url"] == BUCKET_URL

    def test_voucher_without_image_is_fine(self, client, mock_db, supabase_configured):
        response = client.post(
            "/api/v1/vouchers", json=voucher_payload(None), headers=auth_header()
        )
        assert response.status_code == 201
        assert mock_db.vouchers.find_one()["image_url"] is None

    def test_voucher_rejects_foreign_image_host(self, client, supabase_configured):
        response = client.post(
            "/api/v1/vouchers",
            json=voucher_payload("https://evil.example.com/receipt.jpg"),
            headers=auth_header(),
        )
        assert response.status_code == 422

    def test_voucher_rejects_malformed_image_url(self, client):
        response = client.post(
            "/api/v1/vouchers",
            json=voucher_payload("data:image/png;base64,AAAA"),
            headers=auth_header(),
        )
        assert response.status_code == 422

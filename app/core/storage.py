"""FR-5: Receipt image URL validation.

Compression and the upload itself happen client-side (browser-image-compression
-> Supabase Storage). The backend only persists the resulting public URL, so it
validates that the URL is well-formed and — when a Supabase project is
configured — that it actually points into our storage bucket rather than an
arbitrary host.
"""

from urllib.parse import urlparse


def supabase_public_storage_prefix(supabase_url: str) -> str:
    """Public object URL prefix, e.g. https://xyz.supabase.co/storage/v1/object/public/."""
    return f"{supabase_url.rstrip('/')}/storage/v1/object/public/"


def validate_image_url(url: str, supabase_url: str = "") -> str:
    """Raises ValueError so Pydantic surfaces failures as 422 validation errors."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("image_url must be an http(s) URL")

    if supabase_url and not url.startswith(supabase_public_storage_prefix(supabase_url)):
        raise ValueError("image_url must point to the project's Supabase Storage bucket")

    return url

"""Outbound email — pluggable seam for the family invite flow.

The free-tier deployment has no SMTP provider yet, so the default
implementation only logs. Tests (and later a real provider such as Resend or
Supabase's SMTP) replace `send_invite_email`.
"""

import logging

logger = logging.getLogger(__name__)


def send_invite_email(to_email: str, family_name: str, join_code: str) -> None:
    logger.info(
        "Family invite for %s: join '%s' with code %s", to_email, family_name, join_code
    )

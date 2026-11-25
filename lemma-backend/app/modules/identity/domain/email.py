"""Identity email validation and canonicalization."""

from __future__ import annotations

from pydantic import EmailStr, TypeAdapter

_EMAIL_ADAPTER = TypeAdapter(EmailStr)


def normalize_identity_email(email: str | EmailStr) -> str:
    """Return the canonical email representation used for identity matching."""
    validated = _EMAIL_ADAPTER.validate_python(str(email).strip())
    return str(validated).lower()


#: Where Sign in with Apple's "Hide My Email" addresses live. Apple mints one
#: per person per developer team, when that person signs in with Apple, and
#: its relay forwards only mail from sending domains that team registered.
#: Whoever holds one got it from an Apple sign-in, and that is their way back.
APPLE_PRIVATE_RELAY_DOMAIN = "privaterelay.appleid.com"


def is_apple_private_relay(email: str) -> bool:
    """Whether ``email`` is an address Apple's private relay made."""
    return email.rpartition("@")[2].strip().lower() == APPLE_PRIVATE_RELAY_DOMAIN

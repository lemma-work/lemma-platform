"""Identity email validation and canonicalization."""

from __future__ import annotations

from pydantic import EmailStr, TypeAdapter

_EMAIL_ADAPTER = TypeAdapter(EmailStr)


def normalize_identity_email(email: str | EmailStr) -> str:
    """Return the canonical email representation used for identity matching."""
    validated = _EMAIL_ADAPTER.validate_python(str(email).strip())
    return str(validated).lower()

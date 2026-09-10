"""Inputs and limits shared by browser and chat email verification."""

from __future__ import annotations

import re

from email_validator import EmailNotValidError, validate_email

from app.modules.identity.domain.email import normalize_identity_email

CODE_TTL_SECONDS = 600
PENDING_TTL_SECONDS = 1800
RESEND_COOLDOWN_SECONDS = 60
MAX_CODE_ATTEMPTS = 3


def parse_email_reply(message: str | None) -> str | None:
    """Accept one canonical address, allowing punctuation around a typed reply."""
    candidates = [word for word in (message or "").split() if "@" in word]
    if len(candidates) != 1:
        return None
    candidate = candidates[0].strip('.,;:!?<>[]()"“”‘’')
    try:
        result = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError:
        return None
    return normalize_identity_email(result.normalized)


def parse_code_reply(message: str | None) -> str | None:
    """Chatter and ambiguous submissions do not spend a verification attempt."""
    matches = re.findall(r"(?<!\w)[0-9]{6}(?!\w)", message or "")
    return matches[0] if len(matches) == 1 else None

"""The spellings a phone number is looked up under.

Platforms and profiles format the same number differently: Meta's ``wa_id`` is
E.164 without the ``+``, Telegram contacts may carry it or not, and profiles hold
whatever a person typed. Matching compares every spelling.
"""

from __future__ import annotations

import re


def normalize_phone_number(phone: str | None) -> str | None:
    raw = str(phone or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    # Meta wa_id is E.164 without '+'. Telegram contacts may include or omit
    # it. Store one canonical representation while matching profile data by
    # digits so legacy formatting remains compatible.
    return f"+{digits}"


def phone_lookup_candidates(phone: str) -> list[str]:
    normalized = normalize_phone_number(phone)
    if not normalized:
        return []

    digits_only = re.sub(r"\D", "", normalized)
    candidates: list[str] = []
    for candidate in (
        normalized,
        digits_only,
        f"+{digits_only}" if digits_only else None,
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates

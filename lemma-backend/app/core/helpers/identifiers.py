import re

_NON_DIGITS = re.compile(r"\D")


def normalize_mobile_digits(value: str | None) -> str | None:
    """Reduce a phone number to its digits, matching the DB uniqueness index.

    Mirrors ``regexp_replace(mobile_number, '\\D', '', 'g')`` used by
    ``uq_users_mobile_number_digits``. Returns ``None`` when there are no digits.
    """
    if not value:
        return None
    digits = _NON_DIGITS.sub("", value)
    return digits or None


def normalize_mobile_e164(value: str | None) -> str:
    """Normalize a user-supplied international phone number to E.164.

    Lemma deliberately requires an explicit country code. It does not guess a
    country from request locale because that can verify or route the wrong
    number. The length bounds follow E.164's 15-digit maximum while rejecting
    implausibly short values.
    """
    raw = str(value or "").strip()
    digits = _NON_DIGITS.sub("", raw)
    if (
        re.fullmatch(r"\+[0-9() .-]+", raw) is None
        or not 8 <= len(digits) <= 15
        or digits.startswith("0")
    ):
        raise ValueError(
            "Enter a mobile number with its country code, for example +14155552671"
        )
    return f"+{digits}"


def normalize_telegram(value: str | None) -> str | None:
    """Lower-case a telegram username, matching ``ux_users_telegram_username_lower``."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None

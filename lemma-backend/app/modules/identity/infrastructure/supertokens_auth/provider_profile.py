"""Names an identity provider already told us, so signup stops asking for them.

Google, Microsoft and the other OIDC providers send the person's name in the
sign-in payload. Until this module existed it was dropped on the floor and the
first screen after signup asked for a name the provider had just supplied --
the single mandatory question on an otherwise automatic path.

Kept pure and separate from the sign-in override so the shapes can be tested
without standing up SuperTokens.
"""

from __future__ import annotations

from typing import Any

# Long enough for any real name, short enough that a hostile provider payload
# cannot use the column as storage.
MAX_NAME_LENGTH = 128


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    return cleaned[:MAX_NAME_LENGTH]


def split_full_name(full_name: str) -> tuple[str | None, str | None]:
    """First word is the given name, the rest is the family name.

    Wrong for some naming conventions, and deliberately so: the alternative is
    asking, and the point of this module is to stop asking. The user can correct
    it in their profile, which no amount of guessing here would beat.
    """
    parts = full_name.split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return _clean(parts[0]), None
    return _clean(parts[0]), _clean(" ".join(parts[1:]))


def names_from_provider(
    from_id_token_payload: dict[str, Any] | None,
    from_user_info_api: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """``(first_name, last_name)`` from an OIDC payload, or ``(None, None)``.

    The id-token payload is preferred over the userinfo response because it is
    the signed one. Structured claims beat the display name: ``given_name`` and
    ``family_name`` are what the provider actually knows, while ``name`` is a
    formatted string we would have to guess our way back out of.
    """
    for source in (from_id_token_payload, from_user_info_api):
        if not isinstance(source, dict):
            continue

        first = _clean(source.get("given_name"))
        last = _clean(source.get("family_name"))
        if first or last:
            return first, last

        full_name = _clean(source.get("name"))
        if full_name:
            return split_full_name(full_name)

    return None, None

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

from app.modules.agent_surfaces.platforms.common import payload_any

"""Who an email is from, and which thread it belongs to."""


@dataclass(slots=True)
class ParsedEmailIdentity:
    email: str | None = None
    display_name: str | None = None


def _read_email_address(value: Any) -> str | None:
    if isinstance(value, str):
        _, email = parseaddr(value)
        return email or value
    if isinstance(value, dict):
        nested = value.get("emailAddress")
        if isinstance(nested, dict):
            nested_address = nested.get("address")
            if nested_address:
                return str(nested_address)
        return payload_any(value, "email", "address", "email_address")
    return None


def _read_email_name(value: Any) -> str | None:
    if isinstance(value, str):
        name, _ = parseaddr(value)
        return str(name or "").strip() or None
    if isinstance(value, dict):
        nested = value.get("emailAddress")
        if isinstance(nested, dict):
            nested_name = nested.get("name")
            if nested_name:
                return str(nested_name).strip() or None
        return str(value.get("name") or value.get("display_name") or "").strip() or None
    return None


def normalize_email_address(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    return cleaned or None


def parse_email_identity(
    value: Any,
    *,
    fallback_email: Any = None,
    fallback_name: Any = None,
) -> ParsedEmailIdentity:
    display_name = str(fallback_name or "").strip() or None
    email = normalize_email_address(_read_email_address(value))
    if email:
        parsed_name = _read_email_name(value)
        return ParsedEmailIdentity(
            email=email,
            display_name=str(parsed_name or display_name or "").strip() or None,
        )

    fallback_identity = ParsedEmailIdentity(
        email=normalize_email_address(_read_email_address(fallback_email)),
        display_name=display_name,
    )
    return fallback_identity


def email_thread_root(
    *,
    references: list[str],
    in_reply_to: str | None,
    message_id: str | None,
    sender: str | None,
) -> str:
    """The id that groups a mail thread into one conversation.

    The first ``References`` entry is the root of the chain, which is what makes
    a seeded outbound recognisable when the reply comes back. Falls back through
    in-reply-to and this message's own id; a first contact is its own root.
    """
    first_reference = references[0] if references else None
    return first_reference or in_reply_to or message_id or str(sender or "")


def email_sender_authentication(raw_headers, from_address: str | None) -> str:
    """The verdict for this message's ``From:``, as the parsed event records it.

    One helper rather than three, because the deployment's trust configuration
    has to read the same way on every email platform -- an inbound address means
    the same thing whichever mailbox it arrived through.
    """
    from app.modules.agent_surfaces.config import surface_settings
    from app.modules.agent_surfaces.platforms.email_authentication import (
        evaluate_email_authentication,
    )

    trusted = frozenset(
        part.strip().lower()
        for part in str(
            surface_settings.surface_email_trusted_authserv_ids or ""
        ).split(",")
        if part.strip()
    )
    return evaluate_email_authentication(
        raw_headers, from_address=from_address, trusted_authserv_ids=trusted
    ).value

"""Flattening Resend's ``email.received`` webhook for the inbound parser.

Lives beside the Resend parser rather than in the shared webhook controller
because it encodes one provider's envelope, and the thing most worth recording
about that envelope is what it leaves out: **no body, no headers** beyond
``message_id``. Resend documents that you must call the Received Emails API for
the content, which is why ``email_id`` below is the load-bearing field — without
it there is nothing to fetch with, and the agent sees an empty message.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.agent_surfaces.platforms.email_common import parse_email_identity


def email_address(value: Any) -> str | None:
    """Pull a bare address out of a string / ``{address}`` / list shape."""
    if isinstance(value, list):
        return email_address(value[0]) if value else None
    if isinstance(value, dict):
        return email_address(value.get("address") or value.get("email"))
    if isinstance(value, str):
        text = value.strip()
        if "<" in text and ">" in text:
            text = text[text.index("<") + 1 : text.index(">")].strip()
        return text or None
    return None


def all_addresses(value: Any) -> list[str]:
    """Every address in a string / ``{address}`` / list shape, order preserved."""
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(all_addresses(item))
        return found
    single = email_address(value)
    return [single] if single else []


def _header_value(value: Any) -> str:
    """One header as a string, flattening however the provider encoded it.

    ``References`` has arrived in three shapes from Resend: a plain string, a
    JSON array, and — observed live — a *string containing* a JSON array,
    ``'["<a>","<b>"]'``. All three have to collapse to the whitespace-separated
    sequence of message ids that RFC 5322 says the header is, because every
    consumer downstream reads it with ``.split()``. Left unflattened, the entire
    blob becomes one token and is taken for the thread root, so a reply opens a
    new conversation instead of rejoining the one it answers — which is exactly
    what happened to a live notification reply.
    """
    if isinstance(value, (list, tuple)):
        return " ".join(_header_value(item) for item in value if _header_value(item))

    text = str(value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            decoded = json.loads(text)
        except ValueError:
            return text
        if isinstance(decoded, list):
            return " ".join(_header_value(item) for item in decoded if _header_value(item))
    return text


def header_map(raw_headers: Any) -> dict[str, str]:
    """Lower-cased header lookup from a dict or a ``[{name, value}]`` list.

    The webhook never carries headers, so in production this only ever sees the
    dict the Received Emails API returns. The list form costs nothing and is the
    shape most other providers use.
    """
    if isinstance(raw_headers, dict):
        return {str(k).lower(): _header_value(v) for k, v in raw_headers.items()}
    mapped: dict[str, str] = {}
    if isinstance(raw_headers, list):
        for header in raw_headers:
            if isinstance(header, dict) and header.get("name"):
                mapped[str(header["name"]).lower()] = _header_value(header.get("value"))
    return mapped


def references_of(data: dict, headers: dict[str, str]) -> list[str]:
    """The References chain, however this provider encoded it.

    Both sources go through ``_header_value``. The top-level ``data`` copy used
    to be read raw, so the JSON-in-a-string shape was only unwrapped when it
    arrived via headers — the same bug, still live on the sibling path, because
    the test that covered it passed an empty ``data``.
    """
    raw = data.get("references") or headers.get("references") or ""
    return [ref for ref in _header_value(raw).split() if ref]


def normalize_resend_inbound(payload: dict) -> dict:
    """Flatten the webhook envelope into the flat dict the parser consumes.

    Reading ``data.text``/``data.html``/``data.headers`` here — as this once did
    — yields ``None`` every time, which is why every inbound email reached the
    agent as an empty prompt. The body is filled in later by
    ``ResendSurfaceAdapter.enrich_inbound_event``.

    Still tolerant of a flat body and richer address shapes, so an enriched or
    replayed payload normalizes through the same path.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    headers = header_map(data.get("headers"))
    sender = parse_email_identity(data.get("from"))

    # ``to`` is who the sender addressed; ``received_for`` is who it was
    # actually delivered for. They differ under aliasing and forwarding, and the
    # pod's address is in the second — matching only on ``to`` silently dropped
    # that mail as "no surface for address".
    recipients = all_addresses(data.get("to")) + all_addresses(data.get("received_for"))

    return {
        "email_id": str(data.get("email_id") or "").strip() or None,
        "from": sender.email,
        "from_name": sender.display_name,
        "to": recipients[0] if recipients else None,
        "recipients": recipients,
        "subject": data.get("subject") or headers.get("subject"),
        "text": data.get("text"),
        "html": data.get("html"),
        "html_format": data.get("html_format"),
        "message_id": data.get("message_id") or headers.get("message-id"),
        "in_reply_to": data.get("in_reply_to") or headers.get("in-reply-to"),
        "references": references_of(data, headers),
        "attachments": data.get("attachments") or [],
    }


__all__ = [
    "all_addresses",
    "email_address",
    "header_map",
    "normalize_resend_inbound",
    "references_of",
]

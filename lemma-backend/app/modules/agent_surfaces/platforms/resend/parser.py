"""Parse a normalized Resend inbound-email payload into a surface event.

The cloud/native webhook normalizes Resend's ``email.received`` event into a
flat dict: ``{from, to, subject, text, html, message_id, in_reply_to,
references}``. Threading groups by the References root so a reply chain shares
one conversation.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.email_common import (
    email_thread_root,
    inbound_email_text,
    normalize_email_address,
    parse_email_identity,
)
from app.modules.agent_surfaces.platforms.resend.inbound import (
    header_map,
    normalize_attachments,
)


def merge_received_email(
    event: ParsedInboundSurfaceEvent, received: dict[str, Any]
) -> ParsedInboundSurfaceEvent | None:
    """Fold a Received Emails API response into the event the webhook produced.

    Pure, so the part that decides whether a reply is ever seen again can be
    tested without a mail provider. Returns ``None`` when the fetch produced no
    readable body — an agent run with an empty user message is worse than a
    dropped event, because it looks like the agent ignoring somebody.
    """
    if not isinstance(received, dict):
        return None

    message_text = inbound_email_text(
        text=received.get("text"),
        html=received.get("html"),
        html_format=received.get("html_format"),
        subject=received.get("subject") or (event.metadata or {}).get("subject"),
    )
    if not message_text:
        return None

    headers = header_map(received.get("headers"))
    thread = _threading_from_headers(received, headers, event)

    metadata = dict(event.metadata or {})
    metadata.update(thread)
    metadata["attachments"] = _first_non_empty(
        normalize_attachments(received.get("attachments")),
        metadata.get("attachments"),
    )

    reply_target = dict(event.reply_target or {})
    reply_target["in_reply_to"] = thread["message_id"]
    reply_target["references"] = thread["references"]

    identity = parse_email_identity(
        headers.get("from") or received.get("from"),
        fallback_email=event.sender_email,
        fallback_name=event.sender_display_name,
    )

    return event.model_copy(
        update={
            "message_text": message_text,
            "external_thread_id": thread["thread_id"],
            "external_message_id": thread["message_id"],
            "sender_display_name": identity.display_name,
            "reply_target": reply_target,
            "metadata": metadata,
        }
    )


def _first_non_empty(*candidates: Any) -> list:
    for candidate in candidates:
        if candidate:
            return list(candidate)
    return []


def _threading_from_headers(
    received: dict[str, Any],
    headers: dict[str, str],
    event: ParsedInboundSurfaceEvent,
) -> dict[str, Any]:
    """Where this email sits in a thread, once the real headers are in hand.

    Split out because it is the part that decides whether a reply rejoins the
    conversation it answers, and it is worth reading on its own.
    """
    message_id = (
        str(received.get("message_id") or "").strip()
        or headers.get("message-id")
        or event.external_message_id
    )
    in_reply_to = headers.get("in-reply-to") or None
    references = (headers.get("references") or "").split()
    return {
        "thread_id": email_thread_root(
            references=references,
            in_reply_to=in_reply_to,
            message_id=message_id,
            sender=event.sender_external_user_id,
        ),
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references + ([message_id] if message_id else []),
    }


class ResendInboundParser:
    platform = "RESEND"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        if not isinstance(payload, dict):
            return None
        # Tolerant of ``"Name <a@b.com>"``, as the Gmail and Outlook parsers
        # already are — ``normalize_email_address`` alone only lowercases, so a
        # display name would corrupt the sender id and fail identity resolution.
        # ``from_name`` is set when the normalizer already split the display
        # name off; parsing again handles a raw ``"Name <a@b>"`` reaching here.
        identity = parse_email_identity(
            payload.get("from"), fallback_name=payload.get("from_name")
        )
        sender = identity.email
        destination = normalize_email_address(payload.get("to"))
        if not sender or not destination:
            return None

        message_id = str(payload.get("message_id") or "").strip() or None
        in_reply_to = str(payload.get("in_reply_to") or "").strip() or None
        references = [str(r).strip() for r in (payload.get("references") or []) if str(r).strip()]
        thread_root = email_thread_root(
            references=references,
            in_reply_to=in_reply_to,
            message_id=message_id,
            sender=sender,
        )

        subject = str(payload.get("subject") or "").strip() or None
        message_text = inbound_email_text(
            text=payload.get("text"),
            html=payload.get("html"),
            html_format=payload.get("html_format"),
            subject=subject,
        )

        # The outbound reply references chain = inbound references + this id.
        reply_references = references + ([message_id] if message_id else [])

        return ParsedInboundSurfaceEvent(
            platform="RESEND",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_channel_id=destination,
            external_thread_id=thread_root,
            external_message_id=message_id,
            sender_external_user_id=sender,
            sender_email=sender,
            sender_display_name=identity.display_name,
            message_text=message_text,
            is_dm=True,
            should_start_conversation=True,
            reply_target={
                "recipient_email": sender,
                "subject": subject,
                "in_reply_to": message_id,
                "references": reply_references,
            },
            metadata={
                "platform": "RESEND",
                "surface_address": destination,
                "mailbox_email": destination,
                "subject": subject,
                "thread_id": thread_root,
                "message_id": message_id,
                "reply_to_email": sender,
                "in_reply_to": message_id,
                "references": reply_references,
                # The handle the enrichment step needs to fetch the body. The
                # webhook carries no content, so without this the agent sees an
                # empty message.
                "email_id": str(payload.get("email_id") or "").strip() or None,
                "attachments": payload.get("attachments") or [],
            },
        )

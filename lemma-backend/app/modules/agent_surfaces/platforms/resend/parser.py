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
from app.modules.agent_surfaces.platforms.common import text_or_none
from app.modules.agent_surfaces.platforms.email_identity import (
    email_sender_authentication,
    email_thread_root,
    normalize_email_address,
    parse_email_identity,
)
from app.modules.agent_surfaces.platforms.email_text import inbound_email_text
from app.modules.agent_surfaces.platforms.resend.email_recipients import (
    other_people,
    pod_was_addressed,
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

    should_start = _fetched_recipients(
        received,
        reply_target,
        sender=identity.email or event.sender_email or "",
        own_address=str(event.external_channel_id or ""),
        otherwise=event.should_start_conversation,
    )

    return event.model_copy(
        update={
            "should_start_conversation": should_start,
            "message_text": message_text,
            "external_thread_id": thread["thread_id"],
            "external_message_id": thread["message_id"],
            "sender_display_name": identity.display_name,
            "sender_authentication": email_sender_authentication(
                received.get("headers"), identity.email
            ),
            "reply_target": reply_target,
            "metadata": metadata,
        }
    )


def _fetched_recipients(
    received: dict[str, object],
    reply_target: dict[str, object],
    *,
    sender: str,
    own_address: str,
    otherwise: bool,
) -> bool:
    """Who else a fetched email copies, into ``reply_target``; whether it asks us.

    The fetched email is the complete one: its To and Cc win over whatever the
    webhook happened to carry about who else is on the thread. An email that
    names nobody leaves both as the webhook had them.
    """
    to = received.get("to") or []
    cc = received.get("cc") or []
    if not to and not cc:
        return otherwise
    reply_target["cc"] = other_people(
        addressed_to=to, cc=cc, sender=sender, own_address=own_address
    )
    return pod_was_addressed(addressed_to=to, own_address=own_address)


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

        raw_headers = payload.get("headers")
        message_id = text_or_none(payload.get("message_id"))
        in_reply_to = text_or_none(payload.get("in_reply_to"))
        references = [
            str(r).strip() for r in (payload.get("references") or []) if str(r).strip()
        ]
        thread_root = email_thread_root(
            references=references,
            in_reply_to=in_reply_to,
            message_id=message_id,
            sender=sender,
        )

        subject = text_or_none(payload.get("subject"))
        message_text = inbound_email_text(
            text=payload.get("text"),
            html=payload.get("html"),
            html_format=payload.get("html_format"),
            subject=subject,
        )

        # The outbound reply references chain = inbound references + this id.
        reply_references = references + ([message_id] if message_id else [])
        others = other_people(
            addressed_to=payload.get("addressed_to") or [],
            cc=payload.get("cc") or [],
            sender=sender,
            own_address=destination,
        )
        addressed = pod_was_addressed(
            addressed_to=payload.get("addressed_to") or [], own_address=destination
        )

        return ParsedInboundSurfaceEvent(
            platform="RESEND",
            conversation_type=ConversationType.EXTERNAL_DM,
            external_channel_id=destination,
            external_thread_id=thread_root,
            external_message_id=message_id,
            sender_external_user_id=sender,
            sender_email=sender,
            sender_display_name=identity.display_name,
            # Only when this payload actually carries headers. The `email.received`
            # webhook carries none, so on that path this stays None and
            # `merge_received_email` fills it in after the body fetch. Anything
            # that *does* arrive with headers -- the polling receiver, a
            # replayed payload -- gets its verdict here rather than never,
            # which is what left those paths unauthenticated entirely.
            sender_authentication=(
                email_sender_authentication(raw_headers, sender)
                if raw_headers
                else None
            ),
            message_text=message_text,
            is_dm=True,
            # Copied rather than addressed: answered only if a line names the
            # agent, which ingress decides once it knows the agent's name.
            should_start_conversation=addressed,
            reply_target={
                "recipient_email": sender,
                "subject": subject,
                "in_reply_to": message_id,
                "references": reply_references,
                "cc": others,
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
                "email_id": text_or_none(payload.get("email_id")),
                "attachments": payload.get("attachments") or [],
            },
        )

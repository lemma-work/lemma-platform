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
    normalize_email_address,
    plain_text_from_html,
)


class ResendInboundParser:
    platform = "RESEND"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        if not isinstance(payload, dict):
            return None
        sender = normalize_email_address(payload.get("from"))
        destination = normalize_email_address(payload.get("to"))
        if not sender or not destination:
            return None

        message_id = str(payload.get("message_id") or "").strip() or None
        in_reply_to = str(payload.get("in_reply_to") or "").strip() or None
        references = [str(r).strip() for r in (payload.get("references") or []) if str(r).strip()]
        # Group the conversation by the thread root: the first reference, else
        # the in-reply-to, else this message id.
        thread_root = (references[0] if references else None) or in_reply_to or message_id or sender

        text = payload.get("text")
        message_text = str(text).strip() if text else plain_text_from_html(payload.get("html"))
        subject = str(payload.get("subject") or "").strip() or None

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
            sender_display_name=str(payload.get("from_name") or "").strip() or None,
            message_text=message_text or "",
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
            },
        )

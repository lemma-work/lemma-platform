"""WhatsApp Cloud API webhook parsing."""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.common import (
    payload_section,
    payload_text,
)

from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    WHATSAPP_APPROVAL_HEADER,
    WHATSAPP_INTERACTION_SEP,
)


@dataclass(frozen=True, slots=True)
class _WhatsAppEnvelope:
    """The one message a webhook delivery is about, with what surrounds it.

    WhatsApp wraps every message in ``entry[0].changes[0].value.messages[0]``.
    Both parsers here want exactly that, so they unwrap it the same way once.
    """

    entry: dict[str, Any]
    value: dict[str, Any]
    message: dict[str, Any]


def _envelope(payload: dict[str, Any]) -> _WhatsAppEnvelope | None:
    """The message inside a webhook delivery, or None when it carries none."""
    entry_list = payload.get("entry") or []
    if not entry_list:
        return None
    entry = entry_list[0]
    changes = entry.get("changes") or []
    if not changes:
        return None
    value = payload_section(changes[0], "value")
    messages = value.get("messages") or []
    if not messages:
        return None
    return _WhatsAppEnvelope(entry=entry, value=value, message=messages[0])


class WhatsAppMessageParser:
    platform = "WHATSAPP"

    def parse_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """Resolve a button/list reply into an ask_user answer.

        The reply ``id`` carries ``callback_id~header~value``. A reply whose id
        does not decode (a non-Lemma interactive) returns ``None`` so the message
        path handles it as a typed reply by title.
        """
        del headers
        try:
            envelope = _envelope(payload)
            if envelope is None or envelope.message.get("type") != "interactive":
                return None
            msg = envelope.message
            interactive = payload_section(msg, "interactive")
            reply = interactive.get("button_reply") or payload_section(
                interactive, "list_reply"
            )
            parts = payload_text(reply, "id").split(WHATSAPP_INTERACTION_SEP, 2)
            if len(parts) != 3:
                return None
            callback_id, header, answer = parts
            if not callback_id or not header:
                return None

            sender_wa_id = payload_text(msg, "from")
            common: dict[str, Any] = {
                "platform": "WHATSAPP",
                "external_user_id": sender_wa_id or None,
                "external_thread_id": sender_wa_id or None,
                "callback_id": callback_id,
                "reply_target": (
                    {"sender_wa_id": sender_wa_id} if sender_wa_id else {}
                ),
                "dedup_id": payload_text(msg, "id") or None,
                "raw_payload": payload,
            }
            # An approval button reply carries the decision in place of an
            # answer; everything else is an ask_user answer keyed by header.
            if header == WHATSAPP_APPROVAL_HEADER:
                return ParsedSurfaceInteraction(
                    approval_decision=answer or None, **common
                )
            return ParsedSurfaceInteraction(values={header: answer}, **common)
        except Exception:
            return None

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        envelope = _envelope(payload)
        if envelope is None:
            return None

        msg = envelope.message
        value = envelope.value
        message_text, attachments = self._message_body(msg)

        contacts = value.get("contacts") or []
        sender = contacts[0] if contacts else {}
        sender_wa_id = msg.get("from", "")
        sender_name = (sender.get("wa_id") or "").replace("+", "") or sender_wa_id
        waba_id = envelope.entry.get("id")
        phone_number_id = payload_section(value, "metadata").get("phone_number_id")

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=ConversationType.EXTERNAL_DM,
            tenant_id=waba_id,
            external_channel_id=phone_number_id,
            external_thread_id=f"{sender_wa_id}@{phone_number_id or waba_id}",
            external_message_id=msg.get("id"),
            sender_external_user_id=sender_wa_id,
            sender_phone=sender_wa_id,
            sender_display_name=payload_section(sender, "profile").get(
                "name", sender_name
            ),
            message_text=message_text,
            is_dm=True,
            mentioned_agent=True,
            should_start_conversation=True,
            reply_target={
                "phone_number_id": phone_number_id,
                "sender_wa_id": sender_wa_id,
            },
            metadata={
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
                "contacts": contacts,
                "attachments": attachments,
            },
            raw_payload=payload,
        )

    def _message_body(self, msg: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """The readable text of a message, and any attachment, by type.

        WhatsApp puts the readable part somewhere different for each type, and a
        media message's caption is optional -- hence the fall back to the type
        name, so the agent at least knows something arrived.
        """
        msg_type = msg.get("type", "text")
        if msg_type == "text":
            return payload_section(msg, "text").get("body", ""), []
        if msg_type == "interactive":
            return self._interactive_title(payload_section(msg, "interactive")), []
        attachment = self._parse_attachment(msg, msg_type)
        caption = payload_section(msg, "text").get("body", "") or msg_type
        return caption, ([attachment] if attachment else [])

    def _interactive_title(self, interactive: dict[str, Any]) -> str:
        """The label the person tapped, for a button or a list reply."""
        kind = interactive.get("type")
        if kind in ("button_reply", "list_reply"):
            return payload_section(interactive, kind).get("title", "")
        return str(interactive)

    def _parse_attachment(self, msg: dict, msg_type: str) -> dict[str, Any] | None:
        media_data = msg.get(msg_type)
        if not isinstance(media_data, dict):
            return None
        return {
            "id": media_data.get("id"),
            "name": media_data.get("filename") or msg_type,
            "content_type": msg_type,
            "mime_type": media_data.get("mime_type"),
            "size": media_data.get("file_size"),
            "download_url": None,
        }

"""WhatsApp Cloud API webhook parsing."""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WHATSAPP_INTERACTION_SEP,
)


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
            entry = (payload.get("entry") or [{}])[0]
            change = (entry.get("changes") or [{}])[0]
            value = change.get("value") or {}
            messages = value.get("messages") or []
            if not messages:
                return None
            msg = messages[0]
            if msg.get("type") != "interactive":
                return None
            interactive = msg.get("interactive") or {}
            reply = (
                interactive.get("button_reply")
                or interactive.get("list_reply")
                or {}
            )
            reply_id = str(reply.get("id") or "")
            parts = reply_id.split(WHATSAPP_INTERACTION_SEP, 2)
            if len(parts) != 3:
                return None
            callback_id, header, answer = parts
            if not callback_id or not header:
                return None
            sender_wa_id = str(msg.get("from") or "")
            return ParsedSurfaceInteraction(
                platform="WHATSAPP",
                external_user_id=sender_wa_id or None,
                external_thread_id=sender_wa_id or None,
                callback_id=callback_id,
                values={header: answer},
                reply_target={"sender_wa_id": sender_wa_id} if sender_wa_id else {},
                dedup_id=str(msg.get("id") or "") or None,
                raw_payload=payload,
            )
        except Exception:
            return None

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        entry_list = payload.get("entry") or []
        if not entry_list:
            return None

        entry = entry_list[0]
        changes = entry.get("changes") or []
        if not changes:
            return None

        change = changes[0]
        value = change.get("value") or {}
        messages = value.get("messages") or []
        if not messages:
            return None

        msg = messages[0]
        msg_type = msg.get("type", "text")

        message_text = ""
        attachments: list[dict[str, Any]] = []

        if msg_type == "text":
            message_text = (msg.get("text") or {}).get("body", "")
        elif msg_type == "interactive":
            interactive = msg.get("interactive") or {}
            if interactive.get("type") == "button_reply":
                message_text = (interactive.get("button_reply") or {}).get("title", "")
            elif interactive.get("type") == "list_reply":
                message_text = (interactive.get("list_reply") or {}).get("title", "")
            else:
                message_text = str(interactive)
        else:
            attachment = self._parse_attachment(msg, msg_type)
            if attachment:
                attachments.append(attachment)
            message_text = (msg.get("text") or {}).get("body", "") or msg_type

        contacts = value.get("contacts") or []
        sender = contacts[0] if contacts else {}
        sender_wa_id = msg.get("from", "")
        sender_name = (sender.get("wa_id") or "").replace("+", "") or sender_wa_id
        sender_display = (sender.get("profile") or {}).get("name", sender_name)

        waba_id = entry.get("id")
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")

        external_thread_id = f"{sender_wa_id}@{phone_number_id or waba_id}"

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=ConversationType.EXTERNAL_DM,
            tenant_id=waba_id,
            external_channel_id=phone_number_id,
            external_thread_id=external_thread_id,
            external_message_id=msg.get("id"),
            sender_external_user_id=sender_wa_id,
            sender_phone=sender_wa_id,
            sender_display_name=sender_display,
            message_text=message_text,
            is_dm=True,
            mentioned_agent=True,
            should_start_conversation=True,
            reply_target={"phone_number_id": phone_number_id, "sender_wa_id": sender_wa_id},
            metadata={
                "waba_id": waba_id,
                "phone_number_id": phone_number_id,
                "contacts": contacts,
                "attachments": attachments,
            },
            raw_payload=payload,
        )

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

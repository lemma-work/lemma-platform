"""Telegram inbound update parsing."""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)


class TelegramMessageParser:
    platform = "TELEGRAM"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            callback_query = payload.get("callback_query")
            if callback_query:
                message = callback_query.get("message") or {}
                message_text = callback_query.get("data") or ""
                if not message and message_text:
                    return None
            else:
                return None
        else:
            message_text = self._extract_text(message)

        if message is None:
            return None

        if not message_text:
            message_text = ""

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")

        from_user = message.get("from") or {}
        sender_id = str(from_user.get("id", ""))
        sender_display = from_user.get("first_name", "")
        if from_user.get("last_name"):
            sender_display += f" {from_user['last_name']}"
        sender_username = from_user.get("username")
        contact_details = self._extract_contact_details(message=message, sender_id=sender_id)

        is_dm = chat_type == "private"
        conversation_type = (
            ConversationType.EXTERNAL_DM
            if is_dm
            else ConversationType.EXTERNAL_GROUP
        )

        thread_id = str(message.get("message_thread_id") or chat_id)
        message_id = str(message.get("message_id", ""))

        # A mention can be a plain @username (`mention`), a name-link to a
        # username-less user/bot (`text_mention`), or a slash command
        # (`bot_command`). Mentions also live in `caption_entities` for media
        # messages with a caption, so check both entity lists.
        #
        # IMPORTANT: a `mention` entity is just a plain @username — it does NOT
        # indicate *which* user was mentioned. Treating every mention as a bot
        # mention makes the bot wake up whenever anyone @-mentions anyone else in
        # a group. So here we only record that a mention entity exists (and
        # capture the @username / text_mention user id for later verification
        # against the bot's identity in the ingress enrichment step). Only
        # `bot_command` entities are unambiguously directed at this bot.
        _mention_entities = list(message.get("entities") or []) + list(
            message.get("caption_entities") or []
        )
        mentioned_usernames: list[str] = []
        text_mention_user_ids: list[str] = []
        has_bot_command = False
        source_text = message_text or ""
        for entity in _mention_entities:
            entity_type = entity.get("type")
            if entity_type == "bot_command":
                has_bot_command = True
            elif entity_type == "mention":
                username = self._extract_mention_username(entity, source_text)
                if username:
                    mentioned_usernames.append(username)
            elif entity_type == "text_mention":
                user = entity.get("user") or {}
                user_id = str(user.get("id") or "").strip()
                if user_id:
                    text_mention_user_ids.append(user_id)
        # A reply to one of the bot's own messages continues the conversation in
        # a group without re-@mentioning. Telegram privacy mode only delivers
        # replies to THIS bot's messages, so reply_to_message.from.is_bot is a
        # safe signal here.
        reply_to_message = message.get("reply_to_message") or {}
        is_reply_to_bot = bool((reply_to_message.get("from") or {}).get("is_bot"))

        attachments = self._parse_attachments(message)

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=conversation_type,
            tenant_id=None,
            external_channel_id=chat_id,
            external_thread_id=thread_id,
            external_message_id=message_id,
            sender_external_user_id=sender_id,
            sender_phone=contact_details["sender_phone"],
            sender_display_name=sender_display or sender_username,
            message_text=message_text,
            is_dm=is_dm,
            # Only bot commands and DM/reply-to-bot unambiguously address this
            # bot. Generic @username / text_mention mentions are verified
            # against the bot's identity in the ingress enrichment step
            # (_telegram_text_mention_enrich) so the bot doesn't wake up on
            # @mentions of other users in a group.
            mentioned_agent=has_bot_command or is_dm or is_reply_to_bot,
            should_start_conversation=True,
            reply_target={
                "chat_id": chat_id,
                "message_id": message_id,
                # Forum-topic id so replies land in the same topic; empty for
                # ordinary chats.
                "message_thread_id": str(message.get("message_thread_id") or ""),
            },
            metadata={
                "chat_type": chat_type,
                "chat_id": chat_id,
                "is_topic_message": bool(message.get("is_topic_message")),
                "message_thread_id": str(message.get("message_thread_id") or ""),
                "is_thread_reply": is_reply_to_bot,
                "sender_username": sender_username,
                "contact_shared": contact_details["contact_shared"],
                "contact_shared_by_sender": contact_details["contact_shared_by_sender"],
                "shared_contact_phone": contact_details["shared_contact_phone"],
                "attachments": attachments,
                # Carried for the ingress enrichment step to verify against the
                # bot's actual @username / user id.
                "mentioned_usernames": mentioned_usernames,
                "text_mention_user_ids": text_mention_user_ids,
            },
            raw_payload=payload,
        )

    def _extract_text(self, message: dict[str, Any]) -> str:
        return message.get("text") or message.get("caption") or ""

    @staticmethod
    def _extract_mention_username(
        entity: dict[str, Any], text: str
    ) -> str | None:
        """Pull the @username out of a `mention` entity using its offset/length.

        Telegram `mention` entities point at a substring like ``@lemmabot`` in
        the message text/caption. The entity itself carries no user id, so the
        only way to know *who* was mentioned is to slice the text.
        """
        offset = entity.get("offset")
        length = entity.get("length")
        if not isinstance(offset, int) or not isinstance(length, int):
            return None
        if offset < 0 or length <= 0 or offset + length > len(text):
            return None
        substring = text[offset : offset + length]
        if not substring.startswith("@"):
            return None
        return substring[1:].strip().lower() or None

    def _parse_attachments(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        attachments = []
        for key in ("photo", "document", "video", "audio", "voice", "sticker"):
            data = message.get(key)
            if not data:
                continue
            if key == "photo" and isinstance(data, list):
                largest = max(data, key=lambda p: p.get("file_size", 0))
                attachments.append({
                    "file_id": largest.get("file_id"),
                    "name": "photo",
                    "content_type": "image",
                    "size": largest.get("file_size"),
                })
            elif isinstance(data, dict):
                attachments.append({
                    "file_id": data.get("file_id"),
                    "name": data.get("file_name") or key,
                    "content_type": key,
                    "mime_type": data.get("mime_type"),
                    "size": data.get("file_size"),
                })
        return attachments

    def _extract_contact_details(
        self,
        *,
        message: dict[str, Any],
        sender_id: str,
    ) -> dict[str, Any]:
        contact = message.get("contact")
        if not isinstance(contact, dict):
            return {
                "contact_shared": False,
                "contact_shared_by_sender": None,
                "shared_contact_phone": None,
                "sender_phone": None,
            }

        contact_user_id = str(contact.get("user_id") or "").strip() or None
        shared_contact_phone = str(contact.get("phone_number") or "").strip() or None
        shared_by_sender = bool(
            contact_user_id
            and sender_id
            and contact_user_id == sender_id
            and shared_contact_phone
        )
        return {
            "contact_shared": True,
            "contact_shared_by_sender": shared_by_sender,
            "shared_contact_phone": shared_contact_phone,
            "sender_phone": shared_contact_phone if shared_by_sender else None,
        }

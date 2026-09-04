"""Telegram inbound update parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.common import (
    payload_any,
    payload_section,
    payload_text,
)


@dataclass(frozen=True, slots=True)
class _TelegramSender:
    """Who sent the message, as Telegram spells it."""

    user_id: str
    display_name: str
    username: str | None

    @property
    def label(self) -> str | None:
        """A name to show: the full name, else the @username."""
        return self.display_name or self.username


@dataclass(frozen=True, slots=True)
class _TelegramMentions:
    """Who a message appears to address, before the bot's identity is known.

    A ``mention`` entity is a plain @username and does not say *which* user was
    meant, so nothing here counts as addressing this bot on its own. Only
    ``bot_command`` is unambiguous; the rest is carried for the ingress
    enrichment step to check against the bot's own username and id.
    """

    usernames: list[str]
    text_mention_user_ids: list[str]
    has_bot_command: bool


def _sender(message: dict[str, Any]) -> _TelegramSender:
    from_user = payload_section(message, "from")
    display = str(from_user.get("first_name", ""))
    last_name = from_user.get("last_name")
    if last_name:
        display += f" {last_name}"
    return _TelegramSender(
        user_id=str(from_user.get("id", "")),
        display_name=display,
        username=from_user.get("username"),
    )


def _batch(payload: dict[str, Any], message: dict[str, Any]) -> list[Any]:
    """The messages this event covers: a debounced batch, or just the one."""
    batch = payload.get("_lemma_batch_messages")
    if isinstance(batch, list) and batch:
        return batch
    return [message]


def _batch_message_ids(batch: list[Any]) -> list[str]:
    """Every real Telegram message id in the batch, in arrival order."""
    return [
        str(item.get("message_id"))
        for item in batch
        if isinstance(item, dict) and item.get("message_id") is not None
    ]


def _batch_message_id(message: dict[str, Any], batch: list[Any]) -> str:
    """One id for the whole batch, or the single message's own id.

    Synthetic for a batch (``batch:41-43``) because its job is identity --
    dedup and the conversation link -- and no single Telegram id names the
    burst. It is not a message id and must never be sent back to Telegram as
    one; :func:`_reply_to_message_id` is what a reply points at.
    """
    ids = _batch_message_ids(batch)
    if len(ids) > 1:
        return f"batch:{ids[0]}-{ids[-1]}"
    return str(message.get("message_id", ""))


def _reply_to_message_id(message: dict[str, Any], batch: list[Any]) -> str:
    """The message a reply should quote: the last one of the burst.

    Telegram's ``reply_parameters.message_id`` takes an integer id of a real
    message. Handing it the synthetic batch id sent a 400 for every debounced
    burst, so the reply to two photos sent a second apart failed outright.
    """
    ids = _batch_message_ids(batch)
    if ids:
        return ids[-1]
    return str(message.get("message_id", ""))


# A quoted message is context, not a document: enough to know what is being
# pointed at, and not so much that a long quote crowds out the actual request.
_QUOTED_TEXT_LIMIT = 1000


def _quoted_message(message: dict[str, Any]) -> dict[str, Any] | None:
    """The message this one is a reply to, or None when it replies to nothing.

    Telegram delivers the quoted message inline in the update, so this costs
    nothing -- and without it a reply reads as a bare "what about this one?"
    with the "this" missing. The group path had it via ``fetch_thread_context``;
    a DM never called that, which is where replying to an earlier message
    silently lost its subject.
    """
    reply = payload_section(message, "reply_to_message")
    text = str(reply.get("text") or reply.get("caption") or "").strip()
    if not text:
        return None
    from_user = payload_section(reply, "from")
    author = str(from_user.get("username") or from_user.get("first_name") or "").strip()
    return {
        "author": author or None,
        "text": text[:_QUOTED_TEXT_LIMIT],
        "is_bot": bool(from_user.get("is_bot")),
    }


def _media_group_ids(batch: list[Any]) -> list[str]:
    """Album ids in the batch, de-duplicated, in the order they appeared."""
    return list(
        dict.fromkeys(
            str(item.get("media_group_id"))
            for item in batch
            if isinstance(item, dict) and item.get("media_group_id") is not None
        )
    )


#: Every field an inbound message can carry a file in, in the order they are
#: read. ``video_note`` (the round camera messages) and ``animation`` (GIFs) were
#: missing, so both were dropped in silence -- the person watched the agent
#: answer their caption and ignore what they had filmed.
_MEDIA_KEYS = (
    "photo",
    "animation",
    "document",
    "video",
    "video_note",
    "audio",
    "voice",
    "sticker",
)

#: What Telegram sends but never declares. Nothing in the update says a photo is
#: JPEG or a sticker is WebP, and a file with no type is stored as an untyped
#: blob: unviewable, unindexable, and refused by `view_image`.
_UNDECLARED_MIME_TYPES = {
    "photo": "image/jpeg",
    "sticker": "image/webp",
    "video_note": "video/mp4",
}


def _media_keys_in(message: dict[str, Any]) -> tuple[str, ...]:
    """The media fields to read off this message, without reading one twice.

    An animation message carries ``document`` as well, for compatibility with
    clients older than the ``animation`` field. Both point at the same file, so
    reading both saves the GIF twice and tells the agent about two files where
    the person sent one.
    """
    if message.get("animation"):
        return tuple(key for key in _MEDIA_KEYS if key != "document")
    return _MEDIA_KEYS


def _media_mime(key: str, data: dict[str, Any]) -> str | None:
    """What the platform said these bytes are, or what we know it did not say."""
    declared = str(data.get("mime_type") or "").strip()
    if declared:
        return declared
    if key == "sticker" and data.get("is_video"):
        return "video/webm"
    return _UNDECLARED_MIME_TYPES.get(key)


class TelegramMessageParser:
    platform = "TELEGRAM"

    def parse(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        del headers
        message = payload_any(payload, "message", "edited_message")
        if not message:
            # Inline-keyboard taps arrive as ``callback_query`` and are owned by
            # the interaction path (``parse_inbound_interaction``); they are not
            # chat messages, so the message parser ignores them.
            return None

        batch = _batch(payload, message)
        message_text = self._batch_text(batch)
        chat = payload_section(message, "chat")
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")
        is_dm = chat_type == "private"

        sender = _sender(message)
        mentions = self._scan_mentions(message, message_text)
        contact = self._extract_contact_details(
            message=message, sender_id=sender.user_id
        )
        # A reply to one of the bot's own messages continues the conversation in
        # a group without re-@mentioning. Telegram privacy mode only delivers
        # replies to THIS bot's messages, so reply_to_message.from.is_bot is a
        # safe signal here.
        reply_to_message = payload_section(message, "reply_to_message")
        is_reply_to_bot = bool(payload_section(reply_to_message, "from").get("is_bot"))
        message_id = _batch_message_id(message, batch)

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=(
                ConversationType.EXTERNAL_DM
                if is_dm
                else ConversationType.EXTERNAL_GROUP
            ),
            tenant_id=None,
            external_channel_id=chat_id,
            external_thread_id=str(message.get("message_thread_id") or chat_id),
            external_message_id=message_id,
            sender_external_user_id=sender.user_id,
            sender_phone=contact["sender_phone"],
            sender_display_name=sender.label,
            message_text=message_text,
            is_dm=is_dm,
            # Only bot commands and DM/reply-to-bot unambiguously address this
            # bot. Generic @username / text_mention mentions are verified
            # against the bot's identity in the ingress enrichment step
            # (_telegram_text_mention_enrich) so the bot doesn't wake up on
            # @mentions of other users in a group.
            mentioned_agent=mentions.has_bot_command or is_dm or is_reply_to_bot,
            should_start_conversation=True,
            reply_target={
                "chat_id": chat_id,
                "message_id": _reply_to_message_id(message, batch),
                # Forum-topic id so replies land in the same topic; empty for
                # ordinary chats.
                "message_thread_id": payload_text(message, "message_thread_id"),
            },
            metadata={
                "chat_type": chat_type,
                "chat_id": chat_id,
                "is_topic_message": bool(message.get("is_topic_message")),
                "message_thread_id": payload_text(message, "message_thread_id"),
                "is_thread_reply": is_reply_to_bot,
                "quoted_message": _quoted_message(message),
                "sender_username": sender.username,
                "contact_shared": contact["contact_shared"],
                "contact_shared_by_sender": contact["contact_shared_by_sender"],
                "shared_contact_phone": contact["shared_contact_phone"],
                "attachments": self._batch_attachments(batch),
                "batched_message_count": len(batch),
                "media_group_ids": _media_group_ids(batch),
                # Carried for the ingress enrichment step to verify against the
                # bot's actual @username / user id.
                "mentioned_usernames": mentions.usernames,
                "text_mention_user_ids": mentions.text_mention_user_ids,
            },
            raw_payload=payload,
        )

    def _batch_text(self, batch: list[Any]) -> str:
        """The batch as one message: each message's own text, one per line."""
        return "\n".join(
            text
            for item in batch
            if isinstance(item, dict)
            for text in [self._extract_text(item).strip()]
            if text
        )

    def _batch_attachments(self, batch: list[Any]) -> list[Any]:
        return [
            attachment
            for item in batch
            if isinstance(item, dict)
            for attachment in self._parse_attachments(item)
        ]

    def _scan_mentions(
        self, message: dict[str, Any], source_text: str
    ) -> _TelegramMentions:
        """Mention entities on a message, from both lists Telegram uses.

        Mentions also live in ``caption_entities`` for media with a caption, so
        reading only ``entities`` misses half of them.
        """
        entities = list(message.get("entities") or []) + list(
            message.get("caption_entities") or []
        )
        usernames: list[str] = []
        user_ids: list[str] = []
        has_bot_command = False
        for entity in entities:
            entity_type = entity.get("type")
            if entity_type == "bot_command":
                has_bot_command = True
            elif entity_type == "mention":
                username = self._extract_mention_username(entity, source_text)
                if username:
                    usernames.append(username)
            elif entity_type == "text_mention":
                user_id = payload_text(payload_section(entity, "user"), "id").strip()
                if user_id:
                    user_ids.append(user_id)
        return _TelegramMentions(
            usernames=usernames,
            text_mention_user_ids=user_ids,
            has_bot_command=has_bot_command,
        )

    def _extract_text(self, message: dict[str, Any]) -> str:
        return message.get("text") or message.get("caption") or ""

    @staticmethod
    def _extract_mention_username(entity: dict[str, Any], text: str) -> str | None:
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
        for key in _media_keys_in(message):
            data = message.get(key)
            if not data:
                continue
            if key == "photo" and isinstance(data, list):
                largest = max(data, key=lambda p: p.get("file_size", 0))
                attachments.append(
                    {
                        "file_id": largest.get("file_id"),
                        "name": "photo",
                        "content_type": "image",
                        # Telegram re-encodes every inbound photo and then says
                        # nothing about it. Left undeclared, the file is stored
                        # untyped and `view_image` refuses to open it.
                        "mime_type": "image/jpeg",
                        "size": largest.get("file_size"),
                    }
                )
            elif isinstance(data, dict):
                attachments.append(
                    {
                        "file_id": data.get("file_id"),
                        "name": data.get("file_name") or key,
                        "content_type": key,
                        "mime_type": _media_mime(key, data),
                        "size": data.get("file_size"),
                    }
                )
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

        contact_user_id = payload_text(contact, "user_id").strip() or None
        shared_contact_phone = payload_text(contact, "phone_number").strip() or None
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

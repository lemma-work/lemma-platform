"""WhatsApp Cloud API webhook parsing."""

from __future__ import annotations

import re

from app.modules.agent_surfaces.platforms.common import (
    payload_section,
    payload_text,
)

from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.addressing import mentions_number
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    WHATSAPP_APPROVAL_HEADER,
    WHATSAPP_INTERACTION_SEP,
    WHATSAPP_RETRY_HEADER,
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


def split_whatsapp_deliveries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """One webhook body, as one payload per message it carries.

    ``entry``, ``changes`` and ``messages`` are all arrays, and the parser reads
    ``entry[0].changes[0].value.messages[0]`` -- correct for the usual delivery
    of exactly one, and a silent drop of everything after it for any delivery of
    more. Nothing was logged, so a message lost this way was unrecoverable and
    unanswerable: the person saw it sent, and it reached no run.

    Each part keeps the whole envelope (``value.metadata``, ``contacts``) and
    differs only in which single message it holds, so the parser is unchanged
    and every part parses exactly as a single-message delivery does.

    A payload carrying one message -- the overwhelming majority, and every
    ``statuses``-only delivery -- comes back as itself, not a copy.
    """
    entries = payload.get("entry")
    if not isinstance(entries, list) or len(entries) != 1:
        return _split_all(payload, entries)
    changes = entries[0].get("changes") if isinstance(entries[0], dict) else None
    if not isinstance(changes, list) or len(changes) != 1:
        return _split_all(payload, entries)
    messages = payload_section(changes[0], "value").get("messages")
    if not isinstance(messages, list) or len(messages) <= 1:
        return [payload]
    return _split_all(payload, entries)


def _split_all(payload: dict[str, Any], entries: object) -> list[dict[str, Any]]:
    """Every ``(entry, change, message)`` in a delivery, one payload each."""
    if not isinstance(entries, list):
        return [payload]
    parts: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            value = payload_section(change, "value")
            messages = value.get("messages")
            if not isinstance(messages, list) or not messages:
                continue
            for message in messages:
                parts.append(
                    {
                        **payload,
                        "entry": [
                            {
                                **entry,
                                "changes": [
                                    {
                                        **change,
                                        "value": {**value, "messages": [message]},
                                    }
                                ],
                            }
                        ],
                    }
                )
    # A delivery with no message at all (a status callback) still has to reach
    # the parser, which answers None for it. Returning [] here would be a
    # different behaviour, not a smaller one.
    return parts or [payload]


def _is_our_own_number(sender_wa_id: object, value: dict[str, Any]) -> bool:
    """Whether a message names the business's own number as its sender."""
    ours = re.sub(
        r"\D",
        "",
        str(payload_section(value, "metadata").get("display_phone_number") or ""),
    )
    theirs = re.sub(r"\D", "", str(sender_wa_id or ""))
    return bool(ours) and ours == theirs


def _is_undeliverable(msg: dict[str, Any]) -> bool:
    """Whether WhatsApp is reporting a message rather than delivering one.

    Cloud API answers a message it cannot hand over with ``type: "unsupported"``
    and the reason in ``errors`` -- a forwarded sticker, a view-once, a poll.
    There is no media id behind it and nothing to download.
    """
    return msg.get("type") == "unsupported" or bool(msg.get("errors"))


def _undeliverable_notice(msg: dict[str, Any]) -> str:
    """What to tell the agent about a message that never actually arrived.

    Read as ordinary media this produced the bare word ``unsupported`` as the
    person's text -- the type name fallback below, applied to a type that is not
    a kind of content but an error report. The agent then answered it as if they
    had typed it. So say what happened, and say who is saying it: the line lands
    in the transcript where the person's own words go.
    """
    errors = msg.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    reason = ""
    if isinstance(first, dict):
        reason = str(first.get("title") or first.get("message") or "").strip()
    return (
        "(System notice, not the person's words: WhatsApp could not deliver "
        f"their message{f' — {reason}' if reason else ''}. None of its content "
        "reached Lemma.)"
    )


# The message types that carry a downloadable media object. Anything else that
# is not text, a button, or a shared place or contact is not content Lemma can
# read, and must not be read as its own type name.
_MEDIA_TYPES = frozenset({"image", "audio", "video", "document", "sticker", "voice"})

# Deliveries that are not somebody writing to the agent: a reaction to one of
# its messages, and WhatsApp's own notices (a number changed). Read as messages
# they started a model run whose whole input was the word "reaction".
_NOT_A_MESSAGE = frozenset({"reaction", "system"})


def _location_text(location: dict[str, Any]) -> str:
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    place = ", ".join(
        part
        for part in (payload_text(location, "name"), payload_text(location, "address"))
        if part
    )
    coordinates = f"{latitude},{longitude}" if latitude is not None else ""
    if coordinates and place:
        return f"Shared a location: {coordinates} ({place})"
    described = coordinates or place
    return f"Shared a location: {described}" if described else "Shared a location"


def _contacts_text(contacts: object) -> str:
    entries: list[str] = []
    for contact in contacts if isinstance(contacts, list) else []:
        if not isinstance(contact, dict):
            continue
        name = payload_text(payload_section(contact, "name"), "formatted_name")
        phones = [
            payload_text(phone, "phone")
            for phone in contact.get("phones") or []
            if isinstance(phone, dict) and payload_text(phone, "phone")
        ]
        numbers = f"({', '.join(phones)})" if phones else ""
        entry = " ".join(part for part in (name, numbers) if part)
        if entry:
            entries.append(entry)
    return f"Shared contacts: {'; '.join(entries)}" if entries else "Shared a contact"


def _unreadable_notice(msg_type: str) -> str:
    return (
        "(System notice, not the person's words: they sent a WhatsApp "
        f"{msg_type.replace('_', ' ')} message, which Lemma cannot read.)"
    )


def _replies_to(msg: dict[str, object], business_number: str) -> bool:
    """Whether the message quotes one the business sent.

    ``context.from`` names who wrote the quoted message; in a one-to-one chat it
    is the business's display number. Undocumented for groups -- read where it
    would be, and absent it says nothing.
    """
    quoted_from = payload_text(payload_section(msg, "context"), "from")
    digits = "".join(char for char in business_number if char.isdigit())
    return bool(digits) and "".join(c for c in quoted_from if c.isdigit()) == digits


@dataclass(frozen=True, slots=True)
class WhatsAppFailedDelivery:
    """A message Cloud API accepted and later reported it could not deliver."""

    message_id: str
    code: str
    reason: str


def failed_deliveries(payload: dict[str, object]) -> list[WhatsAppFailedDelivery]:
    """Every ``statuses[]`` entry in a delivery that says ``failed``.

    The other statuses (sent, delivered, read) say nothing that needs doing.
    """
    failures = [
        _failed_delivery(status)
        for entry in _dicts(payload.get("entry"))
        for change in _dicts(entry.get("changes"))
        for status in _dicts(payload_section(change, "value").get("statuses"))
        if status.get("status") == "failed"
    ]
    return [failure for failure in failures if failure.message_id]


def _dicts(value: object) -> list[dict[str, object]]:
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _failed_delivery(status: dict[str, object]) -> WhatsAppFailedDelivery:
    errors = _dicts(status.get("errors"))
    first = errors[0] if errors else {}
    return WhatsAppFailedDelivery(
        message_id=payload_text(status, "id"),
        code=payload_text(first, "code"),
        reason=payload_text(first, "title") or payload_text(first, "message"),
    )


def _retry_tap(
    common: dict[str, Any], sender_wa_id: str, phone_number_id: object
) -> ParsedSurfaceInteraction:
    """A "Try again" tap, located the way the chat's DM link is keyed.

    That is the number it arrived on and the sender at that number -- not the
    bare sender every other tap carries, which finds no link for a retry.
    """
    number = str(phone_number_id or "")
    return ParsedSurfaceInteraction(
        action="retry",
        **{
            **common,
            "external_channel_id": number or None,
            "external_thread_id": (
                f"{sender_wa_id}@{number}" if sender_wa_id and number else None
            ),
        },
    )


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
            # The number the tap arrived on, carried the same way `parse` carries
            # it. Without it an acknowledgement has only the configured number to
            # send from, so a button tapped in a chat with a pooled number is
            # answered by a different number entirely -- from the person's side,
            # a stranger replying to something they pressed.
            phone_number_id = payload_section(envelope.value, "metadata").get(
                "phone_number_id"
            )
            reply_target = {
                key: value
                for key, value in (
                    ("phone_number_id", phone_number_id),
                    ("sender_wa_id", sender_wa_id),
                )
                if value
            }
            common: dict[str, Any] = {
                "platform": "WHATSAPP",
                "external_user_id": sender_wa_id or None,
                "external_thread_id": sender_wa_id or None,
                "callback_id": callback_id,
                "reply_target": reply_target,
                "dedup_id": payload_text(msg, "id") or None,
                "raw_payload": payload,
            }
            if header == WHATSAPP_RETRY_HEADER:
                return _retry_tap(common, sender_wa_id, phone_number_id)
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
        if msg.get("type") in _NOT_A_MESSAGE:
            return None
        message_text, attachments = self._message_body(msg)

        contacts = value.get("contacts") or []
        sender = contacts[0] if contacts else {}
        sender_wa_id = msg.get("from", "")
        sender_name = (sender.get("wa_id") or "").replace("+", "") or sender_wa_id
        waba_id = envelope.entry.get("id")
        phone_number_id = payload_section(value, "metadata").get("phone_number_id")
        group_id = payload_text(msg, "group_id").strip()
        if group_id:
            return self._group_message(
                envelope,
                payload=payload,
                group_id=group_id,
                text=message_text,
                attachments=attachments,
            )
        if _is_our_own_number(sender_wa_id, value):
            # Our own number writing to itself: an echo, never a person. An
            # answer would be the bot talking to itself.
            return None

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
                # Carried so "the file never arrived" is answerable from the
                # message row, not only from the agent's guess about it.
                "undeliverable": _is_undeliverable(msg),
            },
            raw_payload=payload,
        )

    def _group_message(
        self,
        envelope: _WhatsAppEnvelope,
        *,
        payload: dict[str, object],
        group_id: str,
        text: str,
        attachments: list[dict[str, object]],
    ) -> ParsedInboundSurfaceEvent:
        """A message somebody wrote in a group the bot is in.

        The group is the channel and the thread: WhatsApp groups have no
        threads, and each sender still gets a conversation of their own because
        links are keyed by sender. The reply goes to the group -- never to the
        sender's own number, which would answer in private a question asked in
        front of everyone.

        Whether it is for the bot is read from the text: an ``@`` of the
        business number, or a reply to the bot's own message. Meta documents
        neither a mention field nor reply context for groups, so both are read
        where they would be and cost nothing when absent; being named in words
        is decided in ingress, where the agent's name is known.
        """
        msg = envelope.message
        value = envelope.value
        metadata = payload_section(value, "metadata")
        phone_number_id = metadata.get("phone_number_id")
        business_number = str(metadata.get("display_phone_number") or "")
        contacts = value.get("contacts") or []
        sender = contacts[0] if contacts else {}
        # With business-scoped user ids a participant may come without a phone
        # number at all, and `from_user_id` is then the only name for them.
        sender_phone = payload_text(msg, "from").strip() or None
        sender_id = sender_phone or payload_text(msg, "from_user_id").strip() or None
        addressed = mentions_number(text, business_number) or _replies_to(
            msg, business_number
        )
        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=ConversationType.EXTERNAL_GROUP,
            tenant_id=envelope.entry.get("id"),
            external_channel_id=group_id,
            external_thread_id=group_id,
            external_message_id=msg.get("id"),
            sender_external_user_id=sender_id,
            sender_phone=sender_phone,
            sender_display_name=payload_section(sender, "profile").get("name")
            or sender_id,
            message_text=text,
            is_dm=False,
            mentioned_agent=addressed,
            should_start_conversation=addressed,
            reply_target={"phone_number_id": phone_number_id, "group_id": group_id},
            metadata={
                "waba_id": envelope.entry.get("id"),
                "phone_number_id": phone_number_id,
                "group_id": group_id,
                "contacts": contacts,
                "attachments": attachments,
                "undeliverable": _is_undeliverable(msg),
            },
            raw_payload=payload,
        )

    def _message_body(self, msg: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """The readable text of a message, and any attachment, by type.

        WhatsApp puts the readable part somewhere different for each type. A
        media message's caption lives on the *media* object -- ``image.caption``,
        never ``text.body``, which a media message does not have at all -- and
        reading the wrong one is why a photo sent with a question arrived at the
        agent as the bare word "image" with the question dropped. The type name
        stays as the fallback for media genuinely sent without a caption, so the
        agent at least knows something arrived -- but only for types that *are*
        content, which is why the undeliverable check comes first.
        """
        msg_type = msg.get("type", "text")
        if msg_type == "text":
            return payload_section(msg, "text").get("body", ""), []
        if msg_type == "interactive":
            return self._interactive_title(payload_section(msg, "interactive")), []
        if _is_undeliverable(msg):
            return _undeliverable_notice(msg), []
        if msg_type == "button":
            # A template quick-reply. What the person tapped is its label; the
            # payload stands in only when the label is missing.
            button = payload_section(msg, "button")
            label = payload_text(button, "text") or payload_text(button, "payload")
            return label, []
        if msg_type == "location":
            return _location_text(payload_section(msg, "location")), []
        if msg_type == "contacts":
            return _contacts_text(msg.get("contacts")), []
        if msg_type not in _MEDIA_TYPES:
            # An order, a product enquiry, or a type added after this was
            # written: its type name used to stand in as the person's words.
            return _unreadable_notice(str(msg_type)), []
        attachment = self._parse_attachment(msg, msg_type)
        caption = (
            str(payload_section(msg, msg_type).get("caption") or "").strip() or msg_type
        )
        return caption, ([attachment] if attachment else [])

    def _interactive_title(self, interactive: dict[str, Any]) -> str:
        """The label the person tapped, for a button or a list reply."""
        kind = interactive.get("type")
        if kind in ("button_reply", "list_reply"):
            return payload_section(interactive, kind).get("title", "")
        return str(interactive)

    def _parse_attachment(self, msg: dict, msg_type: str) -> dict[str, Any] | None:
        """One inbound media object, as an attachment the ingest step can save.

        ``filename`` is sent for documents and for nothing else, so an image or
        a voice note has only its mime type to be named by. Ingest completes the
        name from the mime type of the bytes it actually downloads; the type name
        alone is what the prompt block falls back to when the file is not saved.
        """
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

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
                # Carried so "the file never arrived" is answerable from the
                # message row, not only from the agent's guess about it.
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

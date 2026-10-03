"""WhatsApp Cloud API webhook parsing."""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.common import (
    payload_section,
    payload_text,
)

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.modules.agent_surfaces.domain.addressing import mentions_number
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.whatsapp.message_bodies import (
    is_silent,
    is_undeliverable,
    message_body,
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
                part_value = {
                    **value,
                    "messages": [message],
                    "contacts": _contacts_of(value, message),
                }
                parts.append(
                    {
                        **payload,
                        "entry": [
                            {**entry, "changes": [{**change, "value": part_value}]}
                        ],
                    }
                )
    # A delivery with no message at all (a status callback) still has to reach
    # the parser, which answers None for it. Returning [] here would be a
    # different behaviour, not a smaller one.
    return parts or [payload]


def _contacts_of(
    value: Mapping[str, object], message: object
) -> list[dict[str, object]]:
    """The ``contacts`` entries describing whoever sent ``message``.

    ``contacts`` is one list for the whole delivery, so a batch from two people
    carries both of them, and reading ``contacts[0]`` named every message after
    the first sender -- the display name, and the number a tool send then went
    to. Matched on ``wa_id`` (or the business-scoped ``user_id`` where a person
    has no number). A list that names nobody in particular is left as it was.
    """
    listed = value.get("contacts")
    contacts = (
        [c for c in listed if isinstance(c, dict)] if isinstance(listed, list) else []
    )
    sender = _sender_id(message) if isinstance(message, dict) else ""
    matched = [
        contact
        for contact in contacts
        if sender
        and sender in (payload_text(contact, "wa_id"), payload_text(contact, "user_id"))
    ]
    return matched or contacts


def _sender_id(msg: Mapping[str, object]) -> str:
    """Who wrote a message: their number, else their business-scoped user id.

    With business-scoped user ids a person may come without a phone number at
    all, and ``from_user_id`` is then the only name for them -- in a one-to-one
    chat as much as in a group.
    """
    return (
        payload_text(msg, "from").strip() or payload_text(msg, "from_user_id").strip()
    )


def _sender_contact(
    value: Mapping[str, object], msg: Mapping[str, object]
) -> dict[str, object]:
    """The one ``contacts`` entry for this message's sender, or an empty one."""
    contacts = _contacts_of(value, msg)
    return contacts[0] if len(contacts) == 1 else {}


def _reply_ref(
    msg: Mapping[str, object], business_number: str
) -> dict[str, object] | None:
    """The message this one quotes, as a reference the turn can look up.

    WhatsApp sends only the quoted message's id and author, never its text, so
    this is a pointer: what the quoted message *said* has to come from the
    record of what was sent. A forwarded message carries ``context`` too, with
    no id, and quotes nothing.
    """
    context = payload_section(msg, "context")
    quoted_id = payload_text(context, "id").strip()
    if not quoted_id:
        return None
    return {
        "id": quoted_id,
        "from": payload_text(context, "from").strip() or None,
        "is_bot": _replies_to(msg, business_number),
    }


def _replies_to(msg: Mapping[str, object], business_number: str) -> bool:
    """Whether the message quotes one the business sent.

    ``context.from`` names who wrote the quoted message; in a one-to-one chat it
    is the business's display number. Undocumented for groups -- read where it
    would be, and absent it says nothing.
    """
    quoted_from = payload_text(payload_section(msg, "context"), "from")
    digits = "".join(char for char in business_number if char.isdigit())
    return bool(digits) and "".join(c for c in quoted_from if c.isdigit()) == digits


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

            sender_wa_id = _sender_id(msg)
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
        if envelope is None or is_silent(envelope.message):
            # A reaction, a changed number, the chat being opened: none of them
            # is somebody saying something, and each one used to start a run --
            # or, from a stranger, a signup -- answering a message nobody wrote.
            return None

        msg = envelope.message
        group_id = payload_text(msg, "group_id").strip()
        if group_id:
            return self._group_message(envelope, payload=payload, group_id=group_id)

        value = envelope.value
        message_text, attachments = message_body(msg)
        sender = _sender_contact(value, msg)
        sender_wa_id = _sender_id(msg)
        sender_name = payload_text(sender, "wa_id").replace("+", "") or sender_wa_id
        waba_id = envelope.entry.get("id")
        metadata = payload_section(value, "metadata")
        phone_number_id = metadata.get("phone_number_id")
        business_number = str(metadata.get("display_phone_number") or "")

        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=ConversationType.EXTERNAL_DM,
            tenant_id=waba_id,
            external_channel_id=phone_number_id,
            external_thread_id=f"{sender_wa_id}@{phone_number_id or waba_id}",
            external_message_id=msg.get("id"),
            sender_external_user_id=sender_wa_id,
            # Only a real number is a phone: a business-scoped user id is not
            # something to match an account's mobile number against.
            sender_phone=payload_text(msg, "from").strip() or None,
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
                "contacts": [sender] if sender else [],
                "attachments": attachments,
                # Carried so "the file never arrived" is answerable from the
                # message row, not only from the agent's guess about it.
                "undeliverable": is_undeliverable(msg),
                "reply_ref": _reply_ref(msg, business_number),
            },
            raw_payload=payload,
        )

    def _group_message(
        self,
        envelope: _WhatsAppEnvelope,
        *,
        payload: dict[str, object],
        group_id: str,
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
        text, attachments = message_body(msg)
        metadata = payload_section(value, "metadata")
        phone_number_id = metadata.get("phone_number_id")
        business_number = str(metadata.get("display_phone_number") or "")
        sender = _sender_contact(value, msg)
        sender_phone = payload_text(msg, "from").strip() or None
        sender_id = _sender_id(msg) or None
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
                "contacts": [sender] if sender else [],
                "attachments": attachments,
                "undeliverable": is_undeliverable(msg),
                "reply_ref": _reply_ref(msg, business_number),
            },
            raw_payload=payload,
        )

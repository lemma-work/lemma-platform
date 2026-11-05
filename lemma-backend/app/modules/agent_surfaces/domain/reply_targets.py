"""Where a reply to an event goes, one model per platform.

`ParsedInboundSurfaceEvent.reply_target` is a stored mapping -- it rides in job
payloads and in a conversation link's last event -- so its shape is a stored
contract and does not change here. What was untyped was the reading: a Slack
send read ``.get("thread_ts")``, a Teams one ``.get("service_url")``, and a
misspelt key read as ``None`` and quietly posted to the wrong place. These
models read the mapping once, for the event's own platform; the union is
discriminated by ``platform`` so a caller narrows with ``isinstance`` or a
match and the checker follows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

if TYPE_CHECKING:
    from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _texts(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if item is not None)
    text = _text(value)
    return (text,) if text else ()


class _ReplyTarget(BaseModel):
    model_config = ConfigDict(frozen=True)


class SlackReplyTarget(_ReplyTarget):
    platform: Literal["SLACK"] = "SLACK"
    channel: str | None = None
    #: The thread the reply goes in; the message's own ``ts`` when it began one.
    thread_ts: str | None = None


class TeamsReplyTarget(_ReplyTarget):
    platform: Literal["TEAMS"] = "TEAMS"
    #: The Bot Framework endpoint for this conversation.
    service_url: str | None = None
    conversation_id: str | None = None
    #: The activity a channel reply threads under.
    reply_to_id: str | None = None
    team_id: str | None = None
    team_aad_group_id: str | None = None
    channel_id: str | None = None


class TelegramReplyTarget(_ReplyTarget):
    platform: Literal["TELEGRAM"] = "TELEGRAM"
    chat_id: str | None = None
    #: The message a group reply may quote.
    message_id: str | None = None
    #: A forum topic, so the reply lands in the same one.
    message_thread_id: str | None = None


class WhatsAppReplyTarget(_ReplyTarget):
    platform: Literal["WHATSAPP"] = "WHATSAPP"
    #: The number the message arrived on, which answers it.
    phone_number_id: str | None = None
    sender_wa_id: str | None = None
    #: A group the bot created, when the message came from one.
    group_id: str | None = None


class EmailReplyTarget(_ReplyTarget):
    platform: Literal["RESEND"] = "RESEND"
    recipient_email: str | None = None
    subject: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()
    #: Everybody else on the thread, copied on a reply that is for all of them.
    cc: tuple[str, ...] = ()


ReplyTarget = Annotated[
    SlackReplyTarget
    | TeamsReplyTarget
    | TelegramReplyTarget
    | WhatsAppReplyTarget
    | EmailReplyTarget,
    Field(discriminator="platform"),
]

_ADAPTER: TypeAdapter[ReplyTarget] = TypeAdapter(ReplyTarget)

_TEXT_FIELDS = {
    "SLACK": ("channel", "thread_ts"),
    "TEAMS": (
        "service_url",
        "conversation_id",
        "reply_to_id",
        "team_id",
        "team_aad_group_id",
        "channel_id",
    ),
    "TELEGRAM": ("chat_id", "message_id", "message_thread_id"),
    "WHATSAPP": ("phone_number_id", "sender_wa_id", "group_id"),
    "RESEND": ("recipient_email", "in_reply_to"),
}
#: Read as stored, not trimmed: a subject is quoted back as it was written.
_RAW_FIELDS = {"RESEND": ("subject",)}
_LIST_FIELDS = {"RESEND": ("references", "cc")}


def reply_target_as[T: _ReplyTarget](
    event: ParsedInboundSurfaceEvent, model: type[T]
) -> T:
    """The event's reply target as one platform's model, for that platform's
    adapter. Empty when the event is another platform's, which an adapter is
    never handed."""
    return stored_reply_target_as(str(event.platform), event.reply_target, model)


def stored_reply_target_as[T: _ReplyTarget](
    platform: str, stored: Mapping[str, object] | None, model: type[T]
) -> T:
    """A stored mapping as one platform's model -- an interaction's reply
    target, which is not an event's. Empty when ``platform`` is another's."""
    target = reply_target_for(platform, stored)
    return target if isinstance(target, model) else model()


def copied_on_reply(event: ParsedInboundSurfaceEvent) -> tuple[str, ...]:
    """Everybody else on an email thread, copied on a reply to it.

    Empty on every other platform: only an email reply copies anyone.
    """
    return reply_target_as(event, EmailReplyTarget).cc


def reply_target_of(event: ParsedInboundSurfaceEvent) -> ReplyTarget:
    """The event's reply target, read for its own platform."""
    return reply_target_for(str(event.platform), event.reply_target)


def reply_target_for(platform: str, stored: Mapping[str, object] | None) -> ReplyTarget:
    """A stored reply-target mapping, read as ``platform``'s model."""
    key = platform.upper()
    stored = stored or {}
    fields: dict[str, object] = {"platform": key}
    for name in _TEXT_FIELDS.get(key, ()):
        fields[name] = _text(stored.get(name))
    for name in _RAW_FIELDS.get(key, ()):
        raw = stored.get(name)
        fields[name] = str(raw) if raw is not None else None
    for name in _LIST_FIELDS.get(key, ()):
        fields[name] = _texts(stored.get(name))
    return _ADAPTER.validate_python(fields)


__all__ = [
    "EmailReplyTarget",
    "ReplyTarget",
    "SlackReplyTarget",
    "TeamsReplyTarget",
    "TelegramReplyTarget",
    "WhatsAppReplyTarget",
    "copied_on_reply",
    "reply_target_as",
    "reply_target_for",
    "reply_target_of",
    "stored_reply_target_as",
]

"""The parts of an inbound event that decide whether it is admitted, read as
types rather than as mapping keys. Where a reply goes is `reply_targets`.

`ParsedInboundSurfaceEvent.metadata` and `.reply_target` are stored mappings:
they ride in job payloads and in a conversation link's last event, so their
shape is a stored contract and is not changed here. What was untyped was the
reading. Admission asked `metadata.get("is_thread_reply")` in four modules and
a platform's mention rules in two more, each spelling its own keys; a reply
read `reply_target.get("phone_number_id")` wherever it needed it. These views
are read once from the mapping, so a misspelt key is an attribute error rather
than a silent ``None``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _texts(value: object) -> frozenset[str]:
    if isinstance(value, list | tuple | set | frozenset):
        return frozenset(str(item) for item in value if item is not None)
    return frozenset()


@dataclass(frozen=True, slots=True)
class InboundRoutingFacts:
    """What the parser saw that decides whether a group message is for us."""

    #: A reply inside a thread. Says "a reply", never "a reply to us": whose
    #: thread it is is the conversation link's to answer.
    is_thread_reply: bool = False
    #: The platform's own event type, where it has several (Slack's
    #: ``app_mention`` against a plain ``message``).
    event_type: str | None = None
    #: The conversation's type (Slack ``mpim``, Teams ``groupChat``: a group DM).
    channel_type: str | None = None
    #: Platform user ids @-mentioned in the message.
    mentioned_user_ids: frozenset[str] = frozenset()
    #: @usernames mentioned as text entities (Telegram).
    mentioned_usernames: frozenset[str] = frozenset()
    #: Users mentioned by id without a username (Telegram ``text_mention``).
    text_mention_user_ids: frozenset[str] = frozenset()
    #: The author of the message this one replies to (Telegram).
    reply_to_user_id: str | None = None
    #: The message is a /command (Telegram).
    has_bot_command: bool = False

    @classmethod
    def of(cls, event: ParsedInboundSurfaceEvent) -> Self:
        metadata: Mapping[str, object] = event.metadata or {}
        return cls(
            is_thread_reply=bool(metadata.get("is_thread_reply")),
            event_type=_text(metadata.get("event_type")),
            channel_type=_text(metadata.get("channel_type")),
            mentioned_user_ids=_texts(metadata.get("mentioned_user_ids")),
            mentioned_usernames=_texts(metadata.get("mentioned_usernames")),
            text_mention_user_ids=_texts(metadata.get("text_mention_user_ids")),
            reply_to_user_id=_text(metadata.get("reply_to_user_id")),
            has_bot_command=bool(metadata.get("has_bot_command")),
        )


__all__ = ["InboundRoutingFacts"]

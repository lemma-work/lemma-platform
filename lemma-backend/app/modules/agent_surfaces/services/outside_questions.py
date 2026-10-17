"""A question passed on from somebody outside the pod, on its way to a member.

A run answering a stranger cannot read past what the pod made Public, so what
it cannot answer it passes on, with ``message_user``, to the member who looks
after the group. That message is the one place a stranger's words travel into a
member's own thread -- where the member's agent, acting with all of the
member's access, reads them -- and the answer travels back the other way.

So both directions are framed here, by the server rather than by either run:

* **Where it came from** is read off the asking conversation -- whether it is a
  contact's, which only the server writes -- and its routing link and the group
  that link is in, never off anything the contact's run said. It names the
  group, so a member who looks after several is never left guessing which one a
  question is from.
* **What the stranger said** is shown quoted, every line, under words that say
  it is theirs. A quoted line cannot start a heading or a list item of its own,
  so it cannot pose as anything the member or Lemma wrote.

The answer going back is the member's own words, or words they approved exactly
as their agent drafted them (``NotificationEntity.respond``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.notification import NotificationEntity
from app.modules.channels.contracts import SourceKind
from app.modules.channels.contracts.places import reply_address

#: The most of a stranger's question that is passed on.
MAX_OUTSIDE_QUESTION_CHARS = 1000
#: The longest display name of theirs shown to the member's agent.
_MAX_NAME_CHARS = 80


@dataclass(frozen=True, slots=True)
class OutsideOrigin:
    """Where a question from outside the pod was asked, as routing recorded it."""

    group_title: str | None
    asked_by_name: str | None
    #: A contact's private chat, or a web visitor's: there is no group.
    in_private: bool = False


#: How a notification learns where its asking conversation sits: the origin of
#: a question from outside the pod, or None for everything else.
OutsideOriginReader = Callable[[UUID | None], Awaitable["OutsideOrigin | None"]]


async def outside_origin(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID | None
) -> OutsideOrigin | None:
    """Where a contact's conversation asked a question, or None if it answers a member.

    Whatever the asking run put in its message, a notification it sends is
    from outside the pod exactly when its conversation is a contact's.
    """
    if conversation_id is None:
        return None
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    if conversation is None or conversation.audience.contact_id is None:
        return None
    address = await reply_address(uow, conversation_id)
    heard = (address.ref or {}) if address is not None else {}
    asked_by = heard.get("sender_display_name")
    asked_by_name = _one_line(asked_by if isinstance(asked_by, str) else None, 255)
    if address is None or address.source.kind is not SourceKind.SHARED:
        # A private conversation: a contact's chat or email thread, or a web
        # visitor's. Said so, rather than "in a group" the member would look
        # for and not find.
        return OutsideOrigin(
            group_title=None, asked_by_name=asked_by_name, in_private=True
        )
    return OutsideOrigin(
        group_title=_one_line(address.source.title, 255),
        asked_by_name=asked_by_name,
    )


def quote(text: str, *, limit: int = MAX_OUTSIDE_QUESTION_CHARS) -> str:
    """Somebody else's words as a block quote, every line of it, cut to ``limit``."""
    clipped = text.strip()
    if len(clipped) > limit:
        clipped = clipped[: limit - 1].rstrip() + "…"
    return "\n".join(
        f"> {line}" if line.strip() else ">" for line in clipped.splitlines()
    )


def outside_question_message(
    notification: NotificationEntity, *, agent_name: str | None
) -> str:
    """What the member reads: who asked, where, their words quoted, what happens next."""
    bot = _one_line(agent_name, _MAX_NAME_CHARS) or "The bot"
    if notification.asked_in_private:
        where = "in a private chat"
    elif notification.origin_group_title:
        where = f"in “{notification.origin_group_title}”"
    else:
        where = "in a group"
    who = (
        f"{_one_line(notification.asked_by_name, _MAX_NAME_CHARS)}, who is outside the pod"
        if notification.asked_by_name
        else "someone outside the pod"
    )
    return (
        f"{bot} was asked this {where} by {who}, and could not answer it from "
        f"what the pod has made Public:\n\n{quote(notification.body)}\n\n"
        "Your answer goes back to them only after you approve the exact words."
    )


def _one_line(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split())[:limit]
    return cleaned or None

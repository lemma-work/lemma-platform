"""A question passed on from somebody outside the pod, on its way to a member.

A run answering a stranger cannot read past what the pod made Public, so what
it cannot answer it passes on, with ``message_user``, to the member who looks
after the group. That message is the one place a stranger's words travel into a
member's own thread -- where the member's agent, acting with all of the
member's access, reads them -- and the answer travels back the other way.

So both directions are framed here, by the server rather than by either run:

* **Where it came from** is read off the asking conversation's routing link
  (the ``~outsiders`` key only routing writes) and the group it belongs to --
  never off anything the stranger's run said. It names the group, so a member
  who looks after several is never left guessing which one a question is from.
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
from app.modules.agent_surfaces.domain.groups import OUTSIDERS_LINK_USER
from app.modules.agent_surfaces.domain.notification import NotificationEntity
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.outside_links import (
    links_to_people_outside,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)

#: The most of a stranger's question that is passed on.
MAX_OUTSIDE_QUESTION_CHARS = 1000
#: The longest display name of theirs shown to the member's agent.
_MAX_NAME_CHARS = 80


@dataclass(frozen=True, slots=True)
class OutsideOrigin:
    """Where a question from outside the pod was asked, as routing recorded it."""

    group_title: str | None
    asked_by_name: str | None


#: How a notification learns where its asking conversation sits: the origin of
#: a question from outside the pod, or None for everything else.
OutsideOriginReader = Callable[[UUID | None], Awaitable["OutsideOrigin | None"]]


async def outside_origin(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID | None
) -> OutsideOrigin | None:
    """The group a conversation answers strangers in, or None if it answers members.

    Read off the routing link, which only routing writes: whatever the asking
    run put in its message, a notification it sends is from outside the pod
    exactly when its conversation is the strangers' thread.
    """
    if conversation_id is None:
        return None
    if not await links_to_people_outside(uow.session, conversation_id):
        return None
    links = SurfaceConversationLinkRepository(uow)
    link = await links.get_by_conversation_id(conversation_id)
    if link is None or link.external_user_id != OUTSIDERS_LINK_USER:
        # Outsiders' all the same: the run answers strangers, so its question
        # is one -- just one whose group cannot be named.
        return OutsideOrigin(group_title=None, asked_by_name=None)
    group = (
        await SurfaceGroupRepository(uow.session).get(
            surface_id=link.surface_id,
            external_channel_id=link.external_channel_id,
        )
        if link.external_channel_id
        else None
    )
    last_event = link.last_event or {}
    asked_by = last_event.get("sender_display_name")
    return OutsideOrigin(
        group_title=_one_line(group.title if group is not None else None, 255),
        asked_by_name=_one_line(asked_by if isinstance(asked_by, str) else None, 255),
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
    where = (
        f"in “{notification.origin_group_title}”"
        if notification.origin_group_title
        else "in a group"
    )
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

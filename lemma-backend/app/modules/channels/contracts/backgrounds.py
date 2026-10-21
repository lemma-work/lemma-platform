"""What a run is shown of a channel, kept as references and read when it runs.

A run answering a line is handed the channel's recent lines as background.
What it is handed is chosen as its turn starts -- which lines -- but what they
say is read from the timeline whenever the run's prompt is built
(``read_backgrounds``): the timeline is the only record of what was said. So a
line edited since reads as it says now, a deleted line or a forgotten
contact's is gone, and nothing another person said is copied into the
conversation's own messages to outlive them there.

Who is reading decides what of it they are shown (``domain/projection``),
applied at the reading, not the choosing:

* ``MEMBER_IN_LEMMA`` -- a member's run answering them in Lemma. Every line,
  but not an answer another member's access made (``withheld_from``); what a
  contact wrote is shown marked as said by somebody outside the pod.
* ``MEMBER_IN_GROUP`` -- a member's run answering in a group on a platform,
  where everybody in the group reads the answer. Not what people outside the
  pod wrote, nor the bot's answers to them, nor members' notes: counted, not
  shown.
* ``CONTACT`` -- a contact's run: what the channel lets them read.
* ``PLATFORM`` -- a platform's own history, read live (Slack, Teams): its
  rule was applied as it was read. A line the pod heard is still a reference;
  one it never heard is kept as the platform gave it.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, cast, func, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import ChannelMessage, SenderKind
from app.modules.channels.domain.projection import Reader, may_read, withheld_from
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.message_models import ChannelMessageModel
from app.modules.channels.infrastructure.rows import MAX_LINES
from app.modules.pod.contracts.members import pod_members_among

#: Where a run's message keeps the background it was handed.
BACKGROUND_KEY = "channel_background"

__all__ = [
    "BACKGROUND_KEY",
    "BackgroundItem",
    "BackgroundRefs",
    "BackgroundView",
    "SaidLine",
    "ShownBackground",
    "current_words",
    "heard_at",
    "read_backgrounds",
    "refs_of",
]


class BackgroundView(StrEnum):
    MEMBER_IN_LEMMA = "MEMBER_IN_LEMMA"
    MEMBER_IN_GROUP = "MEMBER_IN_GROUP"
    CONTACT = "CONTACT"
    PLATFORM = "PLATFORM"


class BackgroundItem(BaseModel):
    """One line of a background: a line of the timeline, by reference -- or,
    only for a platform's history the pod never heard, what the platform said."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    line_id: UUID | None = None
    seq: int | None = None
    #: Written outside the pod, as the platform marked it (``PLATFORM``).
    outside_pod: bool = False
    author: str | None = None
    text: str | None = None
    ts: str | None = None


class BackgroundRefs(BaseModel):
    """The background one run was handed, as it keeps it."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    pod_id: UUID
    #: ``None`` only for a platform's history of a chat the pod keeps no
    #: channel for: nothing in it is a line.
    channel_id: UUID | None = None
    view: BackgroundView
    #: The member whose run it is (``MEMBER_IN_LEMMA``).
    user_id: UUID | None = None
    #: The contact whose run it is, and the place they read (``CONTACT``).
    contact_id: UUID | None = None
    source_id: UUID | None = None
    #: How the pod's bot is named in a group.
    agent_name: str | None = None
    #: Left out as it was chosen (``PLATFORM``): counted, never kept.
    withheld: int = 0
    items: tuple[BackgroundItem, ...] = ()


class SaidLine(BaseModel):
    """One line of a background, as the run reads it."""

    model_config = ConfigDict(frozen=True)

    author: str | None
    text: str
    ts: str | None
    outside_pod: bool = False


class ShownBackground(BaseModel):
    model_config = ConfigDict(frozen=True)

    lines: tuple[SaidLine, ...] = ()
    #: How many lines this run was not shown.
    withheld: int = 0


async def current_words(
    uow: SqlAlchemyUnitOfWork, line_ids: Sequence[UUID]
) -> dict[UUID, str]:
    """What these lines say now; nothing for a line deleted, emptied or gone."""
    lines = await LineRepository(uow.session).by_ids(list(dict.fromkeys(line_ids)))
    return {
        line_id: line.text
        for line_id, line in lines.items()
        if not line.is_deleted and line.text.strip()
    }


async def heard_at(
    uow: SqlAlchemyUnitOfWork, *, source_id: UUID, external_message_ids: Sequence[str]
) -> dict[str, BackgroundItem]:
    """Which of these platform messages the pod heard at this place, as
    references to their lines -- by the platform's id for each."""
    wanted = sorted({value for value in external_message_ids if value})[:MAX_LINES]
    if not wanted:
        return {}
    line = ChannelMessageModel
    rows = await uow.session.execute(
        select(line.external_message_id, line.id, line.seq).where(
            line.source_id == source_id,
            # As ``uq_channel_messages_external`` keys it, then exactly.
            _md5_uuid(line.external_message_id).in_(
                [_md5_uuid(value) for value in wanted]
            ),
            line.external_message_id.in_(wanted),
        )
    )
    return {
        external: BackgroundItem(line_id=line_id, seq=seq)
        for external, line_id, seq in rows
        if external
    }


def _md5_uuid(value: object) -> ColumnElement[UUID]:
    return cast(func.md5(value), PG_UUID(as_uuid=True))


def refs_of(lines: Sequence[ChannelMessage]) -> tuple[BackgroundItem, ...]:
    return tuple(BackgroundItem(line_id=line.id, seq=line.seq) for line in lines)


async def read_backgrounds(
    uow: SqlAlchemyUnitOfWork, backgrounds: Sequence[BackgroundRefs]
) -> list[ShownBackground]:
    """Each background as its run is shown it now, in the order given.

    One read of the lines for all of them. Past the most one read returns,
    the earliest backgrounds are read short -- the latest are what a run is
    answering.
    """
    wanted: dict[UUID, None] = {}
    for refs in reversed(backgrounds):
        for item in refs.items:
            if item.line_id is not None and len(wanted) < MAX_LINES:
                wanted.setdefault(item.line_id)
    lines = await LineRepository(uow.session).by_ids(list(wanted))
    members = await _pod_members(uow, backgrounds, lines)
    return [
        _shown(refs, lines, members.get(refs.pod_id, set())) for refs in backgrounds
    ]


async def _pod_members(
    uow: SqlAlchemyUnitOfWork,
    backgrounds: Sequence[BackgroundRefs],
    lines: dict[UUID, ChannelMessage],
) -> dict[UUID, set[UUID]]:
    """Who of the lines' speakers is in the pod now, for the views that ask."""
    asked: dict[UUID, set[UUID]] = {}
    for refs in backgrounds:
        if refs.view not in (BackgroundView.MEMBER_IN_GROUP, BackgroundView.CONTACT):
            continue
        speakers = asked.setdefault(refs.pod_id, set())
        for item in refs.items:
            line = lines.get(item.line_id) if item.line_id else None
            if line is not None and line.sender.user_id is not None:
                speakers.add(line.sender.user_id)
    return {
        pod_id: await pod_members_among(uow, pod_id, speakers)
        for pod_id, speakers in asked.items()
    }


def _shown(
    refs: BackgroundRefs, lines: dict[UUID, ChannelMessage], members: set[UUID]
) -> ShownBackground:
    shown: list[SaidLine] = []
    withheld = refs.withheld
    for item in refs.items:
        if item.line_id is None:
            if refs.view is BackgroundView.PLATFORM and item.text:
                shown.append(
                    SaidLine(
                        author=item.author,
                        text=item.text,
                        ts=item.ts,
                        outside_pod=item.outside_pod,
                    )
                )
            continue
        line = lines.get(item.line_id)
        if line is None or line.is_deleted or not line.text.strip():
            continue
        said = _as_read(refs, item, line, members)
        if said is None:
            withheld += 1
        else:
            shown.append(said)
    return ShownBackground(lines=tuple(shown), withheld=withheld)


def _as_read(
    refs: BackgroundRefs,
    item: BackgroundItem,
    line: ChannelMessage,
    members: set[UUID],
) -> SaidLine | None:
    """The line as this view reads it, or ``None`` where it may not."""
    sender = line.sender
    from_agent = sender.kind is SenderKind.AGENT
    author = (
        (refs.agent_name or sender.display_name or "the bot")
        if from_agent
        else sender.display_name
    )
    # Said by somebody the pod does not hold: a contact, or a member since gone.
    outside = not from_agent and sender.user_id not in members
    match refs.view:
        case BackgroundView.MEMBER_IN_LEMMA:
            shown = refs.user_id is not None and not withheld_from(
                line, user_id=refs.user_id
            )
            outside, author = sender.kind is SenderKind.CONTACT, sender.display_name
        case BackgroundView.MEMBER_IN_GROUP:
            shown = not (
                line.members_only
                or outside
                or (from_agent and not sender.used_a_members_access)
            )
        case BackgroundView.CONTACT:
            shown = refs.contact_id is not None and may_read(
                Reader.contact(refs.contact_id, source_id=refs.source_id), line
            )
        case BackgroundView.PLATFORM:
            shown = True
            outside, author = item.outside_pod, item.author or sender.display_name
    if not shown:
        return None
    return SaidLine(
        author=author,
        text=line.text,
        ts=line.created_at.isoformat(),
        outside_pod=outside,
    )

"""The ``channel`` and ``inbox`` topics' events, each as one reader reads it.

A line on the socket is the REST line: ``message_response`` -- the mapper every
GET of a line uses -- with the same names and the same reader, so an answer
withheld from a member over REST is withheld here, and nothing the socket
sends could not have been read with a GET. A batch is named once for all its
readers, and each line is encoded at most twice: as the readers it is
withheld from see it, and as the rest do.

Cursors are ``"<rev>:<seq>"``: the channel's change number the subscriber is
up to (``infrastructure.revisions``), and its newest line then, which is how a
resumed subscriber is told which lines are new to it (``line.created``) and
which changed since (``line.edited``, ``line.deleted``). Clients treat it as
opaque.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.protocol import dumps
from app.modules.channels.api.access import Caller
from app.modules.channels.api.message_schemas import (
    ChannelMessageResponse,
    hidden_from,
    message_agents,
    message_people,
    message_response,
    wrote,
)
from app.modules.channels.api.views import channel_rows, deliveries_of
from app.modules.channels.domain.entities import (
    Channel,
    ChannelMessage,
    Delivery,
    Principal,
    QuestionState,
)
from app.modules.channels.infrastructure.change_models import ChannelChangeModel
from app.modules.channels.infrastructure.inbox import Totals, Unread
from app.modules.channels.infrastructure.models import ChannelModel
from app.modules.channels.infrastructure.names import Names

#: The states a question stops waiting in.
CLOSED = frozenset(
    {QuestionState.ANSWERED, QuestionState.CANCELLED, QuestionState.EXPIRED}
)


@dataclass(frozen=True, slots=True)
class Head:
    """Where a channel's timeline and change counter stand."""

    rev: int
    seq: int
    #: The change that last moved its own fields or members.
    changed_rev: int = 0

    @property
    def cursor(self) -> str:
        return cursor_of(self.rev, self.seq)


def cursor_of(rev: int, seq: int) -> str:
    return f"{rev}:{seq}"


def parse_cursor(raw: str | None) -> tuple[int, int] | None:
    """A cursor this topic gave out, or ``None`` for anything else."""
    if raw is None:
        return None
    rev, _, seq = raw.partition(":")
    if not rev.isdigit() or not seq.isdigit():
        return None
    return int(rev), int(seq)


async def heads_of(
    uow: SqlAlchemyUnitOfWork, channel_ids: Collection[UUID]
) -> dict[UUID, Head]:
    """Each channel's head; a channel that is gone is absent."""
    if not channel_ids:
        return {}
    rows = await uow.session.execute(
        select(
            ChannelModel.id,
            ChannelModel.last_rev,
            ChannelModel.last_seq,
            ChannelModel.changed_rev,
        ).where(ChannelModel.id.in_(sorted(channel_ids)))
    )
    return {row[0]: Head(int(row[1]), int(row[2]), int(row[3])) for row in rows}


async def pod_heads(uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> dict[UUID, Head]:
    """Every channel of the pod's head: what the inbox watches."""
    rows = await uow.session.execute(
        select(
            ChannelModel.id,
            ChannelModel.last_rev,
            ChannelModel.last_seq,
            ChannelModel.changed_rev,
        ).where(ChannelModel.pod_id == pod_id)
    )
    return {row[0]: Head(int(row[1]), int(row[2]), int(row[3])) for row in rows}


async def changed_heads(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, since: datetime
) -> dict[UUID, Head]:
    """The heads of the pod's channels whose change counter may have moved
    since ``since`` (by the database's clock): every change is a row of the
    change log stamped by that clock (``infrastructure.revisions``), so this
    reads the few channels that moved rather than every channel of the pod."""
    change = ChannelChangeModel
    moved = (
        select(change.channel_id)
        .where(change.pod_id == pod_id, change.at > since)
        .distinct()
    )
    rows = await uow.session.execute(
        select(
            ChannelModel.id,
            ChannelModel.last_rev,
            ChannelModel.last_seq,
            ChannelModel.changed_rev,
        ).where(ChannelModel.id.in_(moved))
    )
    return {row[0]: Head(int(row[1]), int(row[2]), int(row[3])) for row in rows}


def _event(kind: str, /, **fields: JsonValue) -> str:
    return dumps({"type": kind, **fields})


def _line_kind(line: ChannelMessage, since_seq: int) -> str:
    if line.seq > since_seq:
        return "line.created"
    return "line.deleted" if line.is_deleted else "line.edited"


@dataclass(slots=True)
class LinesView:
    """One batch of one channel's changed lines, for any of its readers."""

    channel: Channel
    lines: Sequence[ChannelMessage]
    names: dict[Principal, str | None]
    agents: dict[UUID, str]
    #: The newest line the readers had before this batch.
    since_seq: int
    #: Where each line went outside Lemma: shown to everybody who reads it.
    deliveries: dict[UUID, list[Delivery]] = field(default_factory=dict)
    _encoded: dict[tuple[UUID, bool, bool, bool], list[str]] = field(
        default_factory=dict
    )

    def events_for(self, reader_id: UUID) -> list[str]:
        events: list[str] = []
        for line in self.lines:
            hidden = hidden_from(line, self.channel, reader_id)
            behalf = line.sender.on_behalf_of
            # The member an answer was made for reads it with ``can_share``.
            for_them = behalf is not None and behalf.user_id == reader_id
            key = (line.id, hidden, wrote(line, reader_id), for_them)
            if key not in self._encoded:
                self._encoded[key] = self._encode(line, reader_id)
            events.extend(self._encoded[key])
        return events

    def _encode(self, line: ChannelMessage, reader_id: UUID) -> list[str]:
        shown: ChannelMessageResponse = message_response(
            line,
            self.channel,
            reader_id=reader_id,
            names=self.names,
            agents=self.agents,
            deliveries=self.deliveries.get(line.id, ()),
        )
        encoded = [
            _event(
                _line_kind(line, self.since_seq),
                message=shown.model_dump(mode="json"),
            )
        ]
        if shown.question is not None and shown.question.state in CLOSED:
            encoded.append(
                _event(
                    "question.closed",
                    channel_id=str(line.channel_id),
                    message_id=str(line.id),
                    state=shown.question.state.value,
                )
            )
        return encoded


async def lines_view(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    lines: Sequence[ChannelMessage],
    *,
    since_seq: int,
) -> LinesView:
    names = Names(uow)
    return LinesView(
        channel=channel,
        lines=lines,
        names=await names.of(
            pod_id=channel.pod_id,
            people=[person for line in lines for person in message_people(line)],
        ),
        agents=await names.agents(
            pod_id=channel.pod_id,
            agent_ids=[agent for line in lines for agent in message_agents(line)],
        ),
        since_seq=since_seq,
        deliveries=await deliveries_of(uow, [line.id for line in lines]),
    )


async def channel_updated(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel: Channel
) -> str:
    """The channel as the reader's channel list shows it, with its counts."""
    (row,) = await channel_rows(uow, caller, [channel])
    return _event("channel.updated", channel=row.model_dump(mode="json"))


def read_updated(channel_id: UUID, read_seq: int) -> str:
    return _event("read.updated", channel_id=str(channel_id), read_seq=read_seq)


def inbox_updated(channel_id: UUID, unread: Unread | None, totals: Totals) -> str:
    """One channel's counts for the reader, and their totals across the pod --
    what ``GET /inbox`` and the channel list would say now. ``unread`` absent:
    nothing there counts for them (a channel they can no longer see)."""
    unread = unread or Unread()
    return _event(
        "inbox.updated",
        channel_id=str(channel_id),
        unread={
            "lines": unread.lines,
            "mentions": unread.mentions,
            "waiting": unread.waiting,
            "read_seq": unread.read_seq,
        },
        totals={
            "channels": totals.channels,
            "mentions": totals.mentions,
            "waiting": totals.waiting,
        },
    )


def question_waiting(view: LinesView, reader_id: UUID) -> list[str]:
    """The batch's new questions that wait on the reader, as they read them."""
    waiting: list[str] = []
    for line in view.lines:
        question = line.question
        if (
            line.seq <= view.since_seq
            or question is None
            or question.state is not QuestionState.OPEN
            or question.awaits != Principal.member(reader_id)
        ):
            continue
        shown = message_response(
            line,
            view.channel,
            reader_id=reader_id,
            names=view.names,
            agents=view.agents,
        )
        waiting.append(
            _event(
                "question.waiting",
                channel_id=str(line.channel_id),
                message_id=str(line.id),
                message=shown.model_dump(mode="json"),
            )
        )
    return waiting


def typing_event(
    channel_id: UUID, user: JsonValue, thread_root_id: JsonValue = None
) -> str:
    return _event(
        "typing", channel_id=str(channel_id), user=user, thread_root_id=thread_root_id
    )


def answering_event(
    state: str,
    *,
    channel_id: str,
    message_id: str,
    agent_id: str,
    conversation_id: str | None = None,
    agent_run_id: str | None = None,
) -> str:
    fields: dict[str, JsonValue] = {
        "channel_id": channel_id,
        "message_id": message_id,
        "agent_id": agent_id,
    }
    if conversation_id is not None:
        fields["conversation_id"] = conversation_id
    if agent_run_id is not None:
        fields["agent_run_id"] = agent_run_id
    return _event(f"answering.{state}", **fields)


def streaming_event(
    *,
    channel_id: str,
    message_id: str,
    agent_id: str,
    agent_run_id: str,
    kind: str,
    delta: str,
) -> str:
    return _event(
        "line.streaming",
        channel_id=channel_id,
        message_id=message_id,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
        kind=kind,
        delta=delta,
    )


__all__ = [
    "CLOSED",
    "Head",
    "LinesView",
    "answering_event",
    "changed_heads",
    "channel_updated",
    "cursor_of",
    "heads_of",
    "inbox_updated",
    "lines_view",
    "parse_cursor",
    "pod_heads",
    "question_waiting",
    "read_updated",
    "streaming_event",
    "typing_event",
]

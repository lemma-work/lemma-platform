"""A channel's timeline: lines added in order, read by number.

Every line takes the next number in its channel (``seq``) under the channel
row's lock, so numbers are gapless and a reader resumes exactly where it left
off. A line written twice is one line: a client names its line
(``client_message_id``) and a platform names its message (``source_id`` +
``external_message_id``); either finds the line already there.

A line is heard at a place (``source_id``: a person said it on a platform) or
written in Lemma -- and then perhaps carried to one (``target_source_id``).
Only a person heard at a place says the place is alive (``last_inbound_at``):
a member's line sent there from Lemma opens no platform's window.
An agent's answer in a group showing its answers to everybody is written
shared, here, whoever posts it (``projection.shared_when_posted``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import (
    ChannelKind,
    ChannelMessage,
    QuestionState,
    Sender,
    SenderKind,
)
from app.modules.channels.domain.parts import Part, dump_parts
from app.modules.channels.domain.projection import shared_when_posted
from app.modules.channels.infrastructure.change_models import (
    ChannelChangeModel,
    ChannelThreadModel,
)
from app.modules.channels.infrastructure.deliveries import DeliveryRepository
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelQuestionModel,
    ChannelSourceModel,
)
from app.modules.channels.infrastructure.revisions import HeldHead, hold, next_rev
from app.modules.channels.infrastructure.rows import (
    MAX_LINES,
    message_entity,
    nameless,
    now,
    sender_values,
)

#: The constraints that hold a line to its own channel: its thread, the line
#: it replies to, the place it was heard at or carried to.
_OF_ITS_CHANNEL = frozenset(
    {
        "fk_channel_messages_thread_root",
        "fk_channel_messages_reply_to",
        "fk_channel_messages_source",
        "fk_channel_messages_target_source",
        "fk_channel_threads_root",
    }
)


class LineMisplaced(ValueError):
    """A line names a thread, a line or a place of another channel."""


@dataclass(frozen=True, slots=True)
class NewLine:
    sender: Sender
    text: str
    #: Where it was heard: a person said it there, on a platform.
    source_id: UUID | None = None
    #: Where a line written in Lemma is carried (and its delivery planned).
    target_source_id: UUID | None = None
    members_only: bool = False
    parts: Sequence[Part] = field(default_factory=tuple)
    client_message_id: str | None = None
    conversation_id: UUID | None = None
    agent_run_id: UUID | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    reply_ref: dict[str, object] | None = None
    reply_to_id: UUID | None = None
    thread_root_id: UUID | None = None


def _with_question() -> Select[tuple[ChannelMessageModel, ChannelQuestionModel]]:
    return select(ChannelMessageModel, ChannelQuestionModel).outerjoin(
        ChannelQuestionModel,
        ChannelQuestionModel.message_id == ChannelMessageModel.id,
    )


def _advance(head: HeldHead, row: ChannelMessageModel) -> None:
    """The line is the channel's newest; outside a thread, its last line too.
    A reply moves its root's count: the root is sent again, under a change of
    its own after the reply's."""
    head.last_seq = row.seq
    head.values["last_seq"] = row.seq
    head.take(row.id)
    if row.thread_root_id is None:
        head.values["last_line_id"] = row.id
        head.line_at = row.created_at
    else:
        head.take(row.thread_root_id)
        head.replies += 1
        head.values["replies"] = head.replies


async def _in_its_channel(write: Awaitable[object]) -> None:
    """Run ``write``; a line hung from another channel's thread, line or place
    is refused by the database, and said so here."""
    try:
        await write
    except IntegrityError as refused:
        name = getattr(refused.orig, "constraint_name", None) or str(refused.orig)
        if any(constraint in name for constraint in _OF_ITS_CHANNEL):
            raise LineMisplaced(
                "The line hangs from a thread, line or place of another channel"
            ) from refused
        raise


def _external_is(
    column: InstrumentedAttribute[str | None], value: str
) -> tuple[ColumnElement[bool], ...]:
    """A platform message id, as ``uq_channel_messages_external`` keys it (its
    md5 as a uuid), and exactly."""
    return (
        cast(func.md5(column), PG_UUID(as_uuid=True))
        == cast(func.md5(value), PG_UUID(as_uuid=True)),
        column == value,
    )


class LineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- writing -------------------------------------------------------------------

    async def append(
        self, channel_id: UUID, line: NewLine
    ) -> tuple[ChannelMessage, bool]:
        """Add a line, or return the one this message already made. ``True``
        only for a line written now. Inside ``revisions.holding``: the
        channel's head is written once the post is done."""
        earlier = await self._written_before(channel_id, line)
        if earlier is not None:
            return earlier, False
        head = await hold(self.session, channel_id)
        if head is None:
            raise LookupError("No such channel")
        # Under the lock: a writer that took it first may have written it.
        earlier = await self._written_before(channel_id, line)
        if earlier is not None:
            return earlier, False
        # An answer asked for in front of a group is the group's to read.
        shared = shared_when_posted(
            line.sender, kind=ChannelKind(head.kind), answers_shared=head.answers_shared
        )
        row = ChannelMessageModel(
            channel_id=channel_id,
            pod_id=head.pod_id,
            seq=head.last_seq + 1,
            # Postgres text holds no NUL: a member's is refused at the API,
            # and one heard on a platform is dropped from what it said.
            body=line.text.replace("\x00", ""),
            parts=dump_parts(list(line.parts)),
            source_id=line.source_id,
            target_source_id=line.target_source_id,
            members_only=line.members_only,
            client_message_id=line.client_message_id,
            conversation_id=line.conversation_id,
            agent_run_id=line.agent_run_id,
            external_message_id=line.external_message_id,
            external_thread_id=line.external_thread_id,
            reply_ref=dict(line.reply_ref) if line.reply_ref is not None else None,
            reply_to_id=line.reply_to_id,
            thread_root_id=line.thread_root_id,
            shared_at=now() if shared else None,
            **sender_values(line.sender),
        )
        self.session.add(row)
        await _in_its_channel(self.session.flush())
        _advance(head, row)
        if line.thread_root_id is not None:
            await _in_its_channel(self._count_reply(row))
        if line.source_id is not None and line.sender.kind is not SenderKind.AGENT:
            await self.session.execute(
                update(ChannelSourceModel)
                .where(ChannelSourceModel.id == line.source_id)
                .values(last_inbound_at=row.created_at)
            )
        return message_entity(row), True

    async def _count_reply(self, reply: ChannelMessageModel) -> None:
        """A reply counts on its thread's row, not by rewriting its root."""
        thread = ChannelThreadModel
        statement = insert(thread).values(
            root_id=reply.thread_root_id,
            channel_id=reply.channel_id,
            reply_count=1,
            last_reply_at=reply.created_at,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[thread.root_id],
                set_={
                    "reply_count": thread.reply_count + 1,
                    "last_reply_at": func.greatest(
                        thread.last_reply_at, statement.excluded.last_reply_at
                    ),
                },
            )
        )

    async def named(
        self, channel_id: UUID, client_message_id: str
    ) -> ChannelMessage | None:
        """The channel's line a client named so, if one was written."""
        found = (
            await self.session.execute(
                _with_question().where(
                    ChannelMessageModel.channel_id == channel_id,
                    ChannelMessageModel.client_message_id == client_message_id,
                )
            )
        ).first()
        return message_entity(found[0], question=found[1]) if found else None

    async def _written_before(
        self, channel_id: UUID, line: NewLine
    ) -> ChannelMessage | None:
        message = ChannelMessageModel
        if line.client_message_id is not None:
            row = await self.session.scalar(
                select(message).where(
                    message.channel_id == channel_id,
                    message.client_message_id == line.client_message_id,
                )
            )
            if row is not None:
                return message_entity(row)
        if line.external_message_id is None or line.source_id is None:
            return None
        row = await self.session.scalar(
            select(message).where(
                message.source_id == line.source_id,
                *_external_is(message.external_message_id, line.external_message_id),
            )
        )
        if row is None:
            return None
        return await self._heard_again(row, line)

    async def _heard_again(
        self, row: ChannelMessageModel, line: NewLine
    ) -> ChannelMessage:
        """A platform message heard twice: the second hearing may know more --
        the conversation answering it, what answers it in place, who said it."""
        shown_before = (row.conversation_id, row.sender_name, row.sender_kind)
        if row.conversation_id is None and line.conversation_id is not None:
            row.conversation_id = line.conversation_id
        if row.reply_ref is None and line.reply_ref is not None:
            row.reply_ref = dict(line.reply_ref)
        sender = line.sender
        if nameless(row) and (sender.user_id or sender.contact_id):
            values = sender_values(sender)
            values["sender_name"] = row.sender_name or values["sender_name"]
            for key, value in values.items():
                setattr(row, key, value)
        if (row.conversation_id, row.sender_name, row.sender_kind) != shown_before:
            # Who said it, or who answers it, is what its readers see: they
            # are sent it again. The channel's number is taken before the line
            # is written, the order ``revisions`` keeps.
            await next_rev(self.session, row.channel_id, message_id=row.id)
        await self.session.flush()
        return message_entity(row)

    async def edit(
        self, message_id: UUID, *, text: str, parts: Sequence[Part] | None = None
    ) -> ChannelMessage | None:
        """Change what a line says, unless it was deleted meanwhile."""
        await self._changing(message_id)
        values: dict[str, object] = {"body": text, "edited_at": now()}
        if parts is not None:
            values["parts"] = dump_parts(list(parts))
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.deleted_at.is_(None),
            )
            .values(**values)
            .returning(ChannelMessageModel)
        )
        return message_entity(row) if row is not None else None

    async def delete(
        self, message_id: UUID, *, by_user_id: UUID | None = None
    ) -> ChannelMessage | None:
        """Forget what a line said; it keeps its place in the timeline, and
        who deleted it (``by_user_id``; none when its platform did). A reply
        deleted no longer counts on its thread."""
        await self._changing(message_id)
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.deleted_at.is_(None),
            )
            .values(
                body="",
                parts=[],
                reply_ref=None,
                deleted_at=now(),
                deleted_by_user_id=by_user_id,
            )
            .returning(ChannelMessageModel)
        )
        if row is None:
            return None
        if row.thread_root_id is not None:
            await self._uncount_reply(row)
        return message_entity(row)

    async def _uncount_reply(self, reply: ChannelMessageModel) -> None:
        """A deleted reply leaves its thread's count; the root, whose count
        its readers are shown, is sent again."""
        await next_rev(self.session, reply.channel_id, message_id=reply.thread_root_id)
        thread = ChannelThreadModel
        await self.session.execute(
            update(thread)
            .where(thread.root_id == reply.thread_root_id)
            .values(reply_count=func.greatest(thread.reply_count - 1, 0))
        )

    async def share(self, message_id: UUID) -> ChannelMessage | None:
        """Open an answer to everybody in its channel; ``None`` when it was
        shared or deleted meanwhile -- a second share changes nothing."""
        await self._changing(message_id)
        row = await self.session.scalar(
            update(ChannelMessageModel)
            .where(
                ChannelMessageModel.id == message_id,
                ChannelMessageModel.deleted_at.is_(None),
                ChannelMessageModel.shared_at.is_(None),
            )
            .values(shared_at=now())
            .returning(ChannelMessageModel)
        )
        return message_entity(row) if row is not None else None

    async def _changing(self, message_id: UUID) -> None:
        """The line is about to change: a change of its channel, taken before
        the line is touched (``revisions``)."""
        channel_id = await self.session.scalar(
            select(ChannelMessageModel.channel_id).where(
                ChannelMessageModel.id == message_id
            )
        )
        if channel_id is not None:
            await next_rev(self.session, channel_id, message_id=message_id)

    # -- reading -------------------------------------------------------------------

    async def get(self, message_id: UUID) -> ChannelMessage | None:
        found = (
            await self.session.execute(
                _with_question().where(ChannelMessageModel.id == message_id)
            )
        ).first()
        if found is None:
            return None
        return message_entity(found[0], question=found[1])

    async def counted(self, line: ChannelMessage) -> ChannelMessage:
        """The line with its thread's count, when it is a thread's root: read
        from ``channel_threads`` alone."""
        thread = ChannelThreadModel
        found = (
            await self.session.execute(
                select(thread.reply_count, thread.last_reply_at).where(
                    thread.root_id == line.id
                )
            )
        ).first()
        if found is None:
            return line
        return line.model_copy(
            update={"reply_count": int(found[0]), "last_reply_at": found[1]}
        )

    async def by_ids(self, ids: Sequence[UUID]) -> dict[UUID, ChannelMessage]:
        if not ids:
            return {}
        rows = await self.session.execute(
            _with_question().where(ChannelMessageModel.id.in_(list(ids)[:MAX_LINES]))
        )
        return {row[0].id: message_entity(row[0], question=row[1]) for row in rows}

    async def page(
        self,
        channel_id: UUID,
        *,
        limit: int,
        before_seq: int | None = None,
        after_seq: int | None = None,
        source_id: UUID | None = None,
        top_level: bool = True,
    ) -> list[ChannelMessage]:
        """Lines oldest first: the newest page, or those before or after a
        number. ``source_id``: only what is at that place -- heard there or
        carried there (a contact reading)."""
        message = ChannelMessageModel
        statement = _with_question().where(message.channel_id == channel_id)
        if top_level:
            statement = statement.where(message.thread_root_id.is_(None))
        if source_id is not None:
            statement = statement.where(
                or_(
                    message.source_id == source_id,
                    message.target_source_id == source_id,
                )
            )
        if before_seq is not None:
            statement = statement.where(message.seq < before_seq)
        size = max(1, min(limit, MAX_LINES))
        if after_seq is not None:
            statement = statement.where(message.seq > after_seq)
            rows = list(
                await self.session.execute(statement.order_by(message.seq).limit(size))
            )
        else:
            rows = list(
                await self.session.execute(
                    statement.order_by(message.seq.desc()).limit(size)
                )
            )
            rows.reverse()
        return await self._with_replies(rows)

    async def thread(
        self,
        root_id: UUID,
        *,
        limit: int,
        after_seq: int | None = None,
        before_seq: int | None = None,
        newest: bool = False,
    ) -> list[ChannelMessage]:
        """A thread's replies oldest first: from its start, after
        ``after_seq`` -- or, ``newest`` or before ``before_seq``, the page
        that ends there."""
        message = ChannelMessageModel
        statement = _with_question().where(message.thread_root_id == root_id)
        if after_seq is not None:
            statement = statement.where(message.seq > after_seq)
        if before_seq is not None:
            statement = statement.where(message.seq < before_seq)
        size = max(1, min(limit, MAX_LINES))
        backwards = newest or before_seq is not None
        rows = list(
            await self.session.execute(
                statement.order_by(
                    message.seq.desc() if backwards else message.seq
                ).limit(size)
            )
        )
        if backwards:
            rows.reverse()
        return [message_entity(row[0], question=row[1]) for row in rows]

    async def changed_since(
        self,
        channel_id: UUID,
        *,
        after_rev: int,
        limit: int,
        up_to_rev: int | None = None,
        asked_after_seq: int | None = None,
    ) -> list[ChannelMessage]:
        """Lines of the channel changed after its change ``after_rev`` and up
        to ``up_to_rev``, in the order they last changed -- thread replies
        too. What a live subscriber resuming from there is sent.

        ``limit`` is the caller's own, not ``MAX_LINES``: the live topic asks
        for one more than its batch to learn there are too many, and a cap
        under that would answer "all of them" with some left out.
        ``asked_after_seq``: only lines newer than it holding a question still
        open -- what the inbox looks for new questions in."""
        change = ChannelChangeModel
        last = func.max(change.rev).label("last")
        window = [
            change.channel_id == channel_id,
            change.rev > after_rev,
            change.message_id.is_not(None),
        ]
        if up_to_rev is not None:
            window.append(change.rev <= up_to_rev)
        moved = (
            select(change.message_id, last)
            .where(*window)
            .group_by(change.message_id)
            .order_by(last)
        )
        if asked_after_seq is not None:
            asked = ChannelQuestionModel
            moved = moved.where(
                change.message_id.in_(
                    select(asked.message_id)
                    .join(
                        ChannelMessageModel,
                        ChannelMessageModel.id == asked.message_id,
                    )
                    .where(
                        asked.channel_id == channel_id,
                        ChannelMessageModel.seq > asked_after_seq,
                        asked.state == QuestionState.OPEN.value,
                    )
                )
            )
        page = moved.limit(max(1, limit)).subquery()
        rows = list(
            await self.session.execute(
                _with_question()
                .join(page, page.c.message_id == ChannelMessageModel.id)
                .where(ChannelMessageModel.channel_id == channel_id)
                .order_by(page.c.last)
            )
        )
        return await self._with_replies(rows)

    async def _with_replies(
        self,
        rows: Sequence[Row[tuple[ChannelMessageModel, ChannelQuestionModel | None]]],
    ) -> list[ChannelMessage]:
        """Each line with its thread's count, kept beside the root."""
        roots = [row[0].id for row in rows]
        counts: dict[UUID, tuple[int, datetime | None]] = {}
        if roots:
            thread = ChannelThreadModel
            found = await self.session.execute(
                select(thread.root_id, thread.reply_count, thread.last_reply_at).where(
                    thread.root_id.in_(roots)
                )
            )
            counts = {row[0]: (int(row[1]), row[2]) for row in found}
        return [
            message_entity(
                row[0],
                question=row[1],
                reply_count=counts.get(row[0].id, (0, None))[0],
                last_reply_at=counts.get(row[0].id, (0, None))[1],
            )
            for row in rows
        ]

    async def at(self, source_id: UUID, external_message_id: str) -> UUID | None:
        """The line a platform message is at this source, if it is one: heard
        there, or written in Lemma and delivered there as that message."""
        heard = await self.session.scalar(
            select(ChannelMessageModel.id).where(
                ChannelMessageModel.source_id == source_id,
                *_external_is(
                    ChannelMessageModel.external_message_id, external_message_id
                ),
            )
        )
        if heard is not None:
            return heard
        return await DeliveryRepository(self.session).sent_as(
            source_id, external_message_id
        )

"""Questions lines put to one person: asked, claimed, answered, closed.

An answer is taken in two steps so two people -- or a tap and a reply --
cannot both answer: ``claim`` moves OPEN to ANSWERING for one caller, the
asker takes the answer, and ``settle`` records the answer that took effect
(or ``release`` puts it back to OPEN when the asker refused it, or failed).
A claim older than ``CLAIM_TIMEOUT`` was abandoned by a caller that died: it
may be taken again, it still waits on its person, and the sweeper puts it
back (``release_lapsed``) or expires it (``expire_due``). Every lookup by
ref is by pod: a ref is only unique within one.

What asked a question is typed (``Asker``): a conversation's, or a workflow
run's. Withdrawing what one asked is by that id, never by matching refs; and
one whose conversation or run is deleted has nobody to answer for, so the
sweeper cancels it (``cancel_orphaned``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import (
    Asker,
    Principal,
    Question,
    QuestionAsker,
    QuestionKind,
    QuestionState,
)
from app.modules.channels.infrastructure.access import visible_to
from app.modules.channels.infrastructure.models import (
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.revisions import touch_lines
from app.modules.channels.infrastructure.rows import MAX_LINES, now, question_entity

#: How long one caller may hold a question while its asker takes the answer.
CLAIM_TIMEOUT = timedelta(minutes=1)
#: Questions cancelled per statement; a member or run with more is worked
#: through in turn, in the same transaction.
_CANCEL_BATCH = 500


@dataclass(frozen=True, slots=True)
class NewQuestion:
    kind: QuestionKind
    #: The asker's idempotency key: a second ask with it is the first.
    ref: str
    #: What asks it: the conversation or run whose answer it is.
    asker: Asker
    question: Mapping[str, object]
    awaits: Principal
    batch_id: str | None = None
    expires_at: datetime | None = None


def still_waiting() -> ColumnElement[bool]:
    """Not closed: open, or claimed by an answer that may yet lapse. What a
    person is shown as waiting on them, and what keeps a batch unsettled."""
    return ChannelQuestionModel.state.in_(
        [QuestionState.OPEN.value, QuestionState.ANSWERING.value]
    )


def _lapsed(at: datetime) -> ColumnElement[bool]:
    q = ChannelQuestionModel
    return and_(
        q.state == QuestionState.ANSWERING.value, q.claimed_at < at - CLAIM_TIMEOUT
    )


@dataclass(frozen=True, slots=True)
class BatchProgress:
    asked: int
    waiting: int

    @property
    def settled(self) -> bool:
        """Something was asked, and nothing of it waits any more."""
        return self.asked > 0 and self.waiting == 0


def _asked_by(
    asker: QuestionAsker,
    *,
    conversation_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
) -> ColumnElement[bool]:
    """What one conversation, or one run, asked as ``asker``."""
    q = ChannelQuestionModel
    if (conversation_id is None) == (workflow_run_id is None):
        raise ValueError("Name the asking conversation or the asking run")
    if conversation_id is not None:
        return and_(q.asker == asker.value, q.asker_conversation_id == conversation_id)
    return and_(q.asker == asker.value, q.asker_workflow_run_id == workflow_run_id)


def _orphaned() -> ColumnElement[bool]:
    """Waiting, with the conversation or run that asked deleted since."""
    q = ChannelQuestionModel
    return and_(
        still_waiting(),
        q.asker_conversation_id.is_(None),
        q.asker_workflow_run_id.is_(None),
    )


class QuestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ask(
        self, *, message_id: UUID, channel_id: UUID, pod_id: UUID, new: NewQuestion
    ) -> Question:
        """The line asks this; a second ask with the same ref is the first."""
        q = ChannelQuestionModel
        await self.session.execute(
            insert(q)
            .values(
                message_id=message_id,
                channel_id=channel_id,
                pod_id=pod_id,
                ref=new.ref,
                asker=new.asker.kind.value,
                asker_conversation_id=new.asker.conversation_id,
                asker_workflow_run_id=new.asker.workflow_run_id,
                asker_key=new.asker.key,
                batch_id=new.batch_id,
                kind=new.kind.value,
                question=dict(new.question),
                awaits_user_id=new.awaits.user_id,
                awaits_contact_id=new.awaits.contact_id,
                expires_at=new.expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_channel_questions_ref")
        )
        found = await self.by_ref(pod_id=pod_id, ref=new.ref)
        assert found is not None
        if found.message_id != message_id:
            # A ref is asked once in a pod: a retried post is the same line
            # (``client_message_id``), so another line asking it is a bug in
            # whoever asked -- not a question to hand back silently.
            raise ValueError(f"Question {new.ref!r} was asked by another line")
        return found

    async def by_ref(self, *, pod_id: UUID, ref: str) -> Question | None:
        row = await self.session.scalar(
            select(ChannelQuestionModel).where(
                ChannelQuestionModel.pod_id == pod_id, ChannelQuestionModel.ref == ref
            )
        )
        return question_entity(row) if row else None

    async def of_message(self, message_id: UUID) -> Question | None:
        row = await self.session.get(ChannelQuestionModel, message_id)
        return question_entity(row) if row else None

    # -- answering ------------------------------------------------------------------

    async def claim(self, message_id: UUID, *, at: datetime) -> Question | None:
        """Take the question to answer it; ``None`` if somebody else has it or
        it is closed."""
        q = ChannelQuestionModel
        await touch_lines(self.session, [message_id])
        row = await self.session.scalar(
            update(q)
            .where(
                q.message_id == message_id,
                or_(q.state == QuestionState.OPEN.value, _lapsed(at)),
            )
            .values(state=QuestionState.ANSWERING.value, claimed_at=at)
            .returning(q)
        )
        return question_entity(row) if row is not None else None

    async def release(self, message_id: UUID) -> None:
        """The asker refused the answer, or failed: the question is open again."""
        q = ChannelQuestionModel
        await touch_lines(self.session, [message_id])
        await self.session.execute(
            update(q)
            .where(q.message_id == message_id, q.state == QuestionState.ANSWERING.value)
            .values(state=QuestionState.OPEN.value, claimed_at=None)
        )

    async def settle(
        self,
        message_id: UUID,
        *,
        state: QuestionState,
        answer: Mapping[str, object] | None,
        by: Principal | None,
    ) -> Question | None:
        """Close a question with what took effect; nothing if already closed.
        Returns it closed now, or ``None``."""
        q = ChannelQuestionModel
        await touch_lines(self.session, [message_id])
        row = await self.session.scalar(
            update(q)
            .where(q.message_id == message_id, still_waiting())
            .values(
                state=state.value,
                answer=dict(answer) if answer is not None else None,
                answered_by_user_id=by.user_id if by else None,
                answered_by_contact_id=by.contact_id if by else None,
                answered_at=now(),
                claimed_at=None,
            )
            .returning(q)
        )
        return question_entity(row) if row is not None else None

    async def settle_ref(
        self,
        *,
        pod_id: UUID,
        ref: str,
        state: QuestionState,
        answer: Mapping[str, object] | None = None,
        by: Principal | None = None,
    ) -> Question | None:
        found = await self.by_ref(pod_id=pod_id, ref=ref)
        if found is None:
            return None
        return await self.settle(found.message_id, state=state, answer=answer, by=by)

    async def cancel_asked_by(
        self,
        asker: QuestionAsker,
        *,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
    ) -> list[Question]:
        """Cancel every question still waiting that this conversation, or this
        run, asked as ``asker``."""
        return await self._cancel(
            and_(
                _asked_by(
                    asker,
                    conversation_id=conversation_id,
                    workflow_run_id=workflow_run_id,
                ),
                still_waiting(),
            )
        )

    async def cancel_orphaned(self, *, limit: int) -> list[Question]:
        """Cancel up to ``limit`` questions whose asker is gone, oldest first."""
        q = ChannelQuestionModel
        orphans = list(
            await self.session.scalars(
                select(q.message_id)
                .where(_orphaned())
                .order_by(q.created_at)
                .limit(limit)
            )
        )
        if not orphans:
            return []
        return await self._cancel(and_(q.message_id.in_(orphans), _orphaned()))

    async def cancel_awaiting(self, *, pod_id: UUID, user_id: UUID) -> list[Question]:
        """Cancel every question still waiting on this member."""
        q = ChannelQuestionModel
        return await self._cancel(
            and_(q.pod_id == pod_id, q.awaits_user_id == user_id, still_waiting())
        )

    async def _cancel(self, which: ColumnElement[bool]) -> list[Question]:
        q = ChannelQuestionModel
        cancelled: list[Question] = []
        while True:
            # A batch at a time, each re-read: a cancelled row no longer
            # matches, so the next read finds the rest.
            batch = list(
                await self.session.scalars(
                    select(q.message_id)
                    .where(which)
                    .order_by(q.message_id)
                    .limit(_CANCEL_BATCH)
                )
            )
            if not batch:
                return cancelled
            # The channels first, then the questions -- the order an answer
            # takes them in -- with the condition re-checked in the update.
            await touch_lines(self.session, batch)
            rows = await self.session.scalars(
                update(q)
                .where(q.message_id.in_(batch), which)
                .values(
                    state=QuestionState.CANCELLED.value,
                    answered_at=now(),
                    claimed_at=None,
                )
                .returning(q)
            )
            cancelled.extend(question_entity(row) for row in rows)
            if len(batch) < _CANCEL_BATCH:
                return cancelled

    async def expire_due(self, *, at: datetime, limit: int) -> list[Question]:
        """Close the questions whose time is up, oldest first -- open ones, and
        ones whose claim lapsed; an answer being taken right now is let finish."""
        q = ChannelQuestionModel
        still_due = and_(
            or_(q.state == QuestionState.OPEN.value, _lapsed(at)), q.expires_at <= at
        )
        due = list(
            await self.session.scalars(
                select(q.message_id)
                .where(still_due)
                .order_by(q.expires_at)
                .limit(limit)
            )
        )
        if not due:
            return []
        # The channels first, then the questions -- the order an answer takes
        # them in -- and the expiry re-checked in the update, so a question
        # answered meanwhile stays answered.
        await touch_lines(self.session, due)
        rows = await self.session.scalars(
            update(q)
            .where(q.message_id.in_(due), still_due)
            .values(state=QuestionState.EXPIRED.value, answered_at=at, claimed_at=None)
            .returning(q)
        )
        return [question_entity(row) for row in rows]

    async def release_lapsed(self, *, at: datetime, limit: int) -> int:
        """Open again the questions whose claim lapsed; how many."""
        q = ChannelQuestionModel
        lapsed = list(
            await self.session.scalars(
                select(q.message_id)
                .where(_lapsed(at))
                .order_by(q.claimed_at)
                .limit(limit)
            )
        )
        if not lapsed:
            return 0
        await touch_lines(self.session, lapsed)
        released = await self.session.scalars(
            update(q)
            .where(q.message_id.in_(lapsed), _lapsed(at))
            .values(state=QuestionState.OPEN.value, claimed_at=None)
            .returning(q.message_id)
        )
        return len(list(released))

    # -- reading --------------------------------------------------------------------

    async def asked_by(
        self,
        asker: QuestionAsker,
        *,
        conversation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        open_only: bool,
        limit: int = MAX_LINES,
    ) -> list[Question]:
        """What this conversation, or this run, asked as ``asker``, newest
        first."""
        q = ChannelQuestionModel
        statement = select(q).where(
            _asked_by(
                asker, conversation_id=conversation_id, workflow_run_id=workflow_run_id
            )
        )
        if open_only:
            statement = statement.where(still_waiting())
        rows = await self.session.scalars(
            statement.order_by(q.created_at.desc()).limit(max(1, min(limit, MAX_LINES)))
        )
        return [question_entity(row) for row in rows]

    async def batch_progress(self, *, pod_id: UUID, batch_id: str) -> BatchProgress:
        """How many questions a batch asked, and how many still wait."""
        q = ChannelQuestionModel
        asked, waiting = (
            await self.session.execute(
                select(func.count(), func.count().filter(still_waiting())).where(
                    q.pod_id == pod_id, q.batch_id == batch_id
                )
            )
        ).one()
        return BatchProgress(asked=int(asked or 0), waiting=int(waiting or 0))

    async def waiting_on(
        self, viewer: Viewer, *, limit: int = MAX_LINES
    ) -> list[Question]:
        """Questions waiting on this member, in channels they may see, newest first."""
        q = ChannelQuestionModel
        rows = await self.session.scalars(
            select(q)
            .join(ChannelModel, ChannelModel.id == q.channel_id)
            .where(
                q.awaits_user_id == viewer.user_id,
                q.pod_id == viewer.pod_id,
                still_waiting(),
                visible_to(viewer),
            )
            .order_by(q.created_at.desc())
            .limit(max(1, min(limit, MAX_LINES)))
        )
        return [question_entity(row) for row in rows]

    async def waiting_on_contact(self, contact_id: UUID) -> list[Question]:
        q = ChannelQuestionModel
        rows = await self.session.scalars(
            select(q)
            .where(
                q.awaits_contact_id == contact_id,
                still_waiting(),
            )
            .order_by(q.created_at.desc())
            .limit(MAX_LINES)
        )
        return [question_entity(row) for row in rows]

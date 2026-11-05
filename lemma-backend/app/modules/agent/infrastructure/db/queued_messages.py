"""Messages sent while a run was working, and who has delivered them.

One place for the queue: the predicate every party agrees on, counting what is
still owed an answer, claiming messages into a run, marking them as waiting on
a question, and the three writes the Agent Host's steering added -- marking a
message as on its way to a host, recording what the host said, and withdrawing
one nobody is carrying yet. All of it is about a message's ``queue_state``
(``domain/queued_messages.py``).

Every write here is one ``UPDATE ... RETURNING`` (or ``DELETE ... RETURNING``)
whose ``WHERE`` restates the state it moves the message out of. That is what
makes each transition happen at most once when two parties race for the same
message -- the in-process capability and the follow-up turn, a host reporting a
steer and the person withdrawing it -- without anything taking a lock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.value_objects import (
    UNANSWERED_QUEUE_STATES,
    AgentRunStatus,
    MessageRole,
    QueueState,
)
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel


def in_order(rows) -> list[MessageEntity]:
    return sorted((row.to_entity() for row in rows), key=lambda item: item.sequence)


def unclaimed_queued_messages(
    agent_run_id: UUID,
    *,
    notes: bool | None = None,
    as_run: bool = False,
    after_sequence: int | None = None,
):
    """Messages that arrived after this run started and nobody has read yet.

    ``start`` queues a message it appends to a run already in flight,
    because that run loaded its history before the message existed. Whoever
    delivered it into a model request marks it steered, so the same predicate
    answers both questions that matter: what to steer in next, and what is
    still owed an answer once the run ends.

    ``as_run`` keeps to messages of the run's own kind, which is what may be
    steered into it: a private note must not shape an answer that goes to the
    platform, and a message meant for the platform must not be answered where
    nobody on it will see. ``notes`` asks for one kind outright -- for a
    follow-up turn, which has not started yet. Steering also needs the run to
    still be answering: a message sent after Stop must not be taken into the
    run being stopped, where nothing would answer it.

    ``after_sequence`` keeps to messages sent after that point -- after Stop,
    for the turn that answers what was said once the person had stopped.
    """
    criteria = (
        MessageModel.agent_run_id == agent_run_id,
        MessageModel.role == MessageRole.USER.value,
        MessageModel.queue_state.in_(UNANSWERED_QUEUE_STATES),
        *(
            (MessageModel.sequence > after_sequence,)
            if after_sequence is not None
            else ()
        ),
    )
    if notes is not None:
        return (*criteria, _is_note() if notes else ~_is_note())
    if as_run:
        run = (
            select(AgentRunModel.private_note.label("private"), AgentRunModel.status)
            .where(AgentRunModel.id == agent_run_id)
            .subquery()
        )
        answering = (
            select(run.c.private).where(run.c.status == AgentRunStatus.RUNNING.value)
        ).scalar_subquery()
        return (*criteria, _is_note() == answering)
    return criteria


def _is_note():
    return MessageModel.private_note


class QueuedMessages(UnitOfWorkRepository):
    """The queue behind a run: counting, claiming, steering, withdrawing."""

    async def count_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        notes: bool | None = None,
        after_sequence: int | None = None,
    ) -> int:
        """How many of this run's queued messages are still unanswered.

        Counted over ``ix_agent_message_run_sequence`` rather than loading the
        run's messages, and asked once when a run ends -- so the answer is
        normally zero and costs one indexed aggregate.

        A message handed to the host as a steer that never landed still counts:
        ``steer_dispatched_at`` is not a claim, so the follow-up turn is what
        answers it.
        """
        return int(
            await self.session.scalar(
                select(func.count()).where(
                    *unclaimed_queued_messages(
                        agent_run_id, notes=notes, after_sequence=after_sequence
                    )
                )
            )
            or 0
        )

    async def earliest_queued_is_note(
        self, agent_run_id: UUID, *, after_sequence: int | None = None
    ) -> bool | None:
        """Whether the oldest unanswered message queued behind this run is a
        private note; None when nothing is queued. A follow-up turn answers one
        kind, and the oldest message decides which goes first."""
        flag = await self.session.scalar(
            select(MessageModel.private_note)
            .where(
                *unclaimed_queued_messages(agent_run_id, after_sequence=after_sequence)
            )
            .order_by(MessageModel.sequence)
            .limit(1)
        )
        return None if flag is None else bool(flag)

    async def claim_queued_user_messages(
        self,
        agent_run_id: UUID,
        *,
        into_run_id: UUID | None = None,
        message_ids: list[UUID] | None = None,
        notes: bool | None = None,
        as_run: bool = False,
        after_sequence: int | None = None,
    ) -> list[MessageEntity]:
        """Take the messages that arrived mid-run, and mark them taken.

        Claimed and read in one statement so a message can be delivered into the
        model exactly once. Whoever claims them owes the person an answer: if
        the run then dies without replying, the row stays claimed and the
        completion sweep will not pick it up either -- but the run is FAILED, so
        the person is told, which is the recovery that was already there.

        ``into_run_id`` is the run that will deliver them, when that is not the
        run they were queued behind: the follow-up turn claims its predecessor's
        queue, and the Agent Host harness reads that claim to know which
        messages its prompt has to carry.

        ``message_ids`` narrows the claim to messages the caller has in hand --
        an Agent Host dispatch claims the ones its prompt already carries, and
        must not claim one that arrived a moment later and is not in it.

        Returns them in the order they were appended, which the caller relies on
        to keep several bubbles of one message in sequence.
        """
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(
                        agent_run_id,
                        notes=notes,
                        as_run=as_run,
                        after_sequence=after_sequence,
                    ),
                    *(
                        (MessageModel.id.in_(message_ids),)
                        if message_ids is not None
                        else ()
                    ),
                )
                .values(
                    queue_state=QueueState.STEERED,
                    steered_into_run_id=into_run_id or agent_run_id,
                )
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return in_order(rows)

    async def mark_queued_waiting_on_answer(
        self, agent_run_id: UUID
    ) -> list[MessageEntity]:
        """Mark what is queued behind a run that ended on a question.

        Nothing answers those until the person answers the question, and
        without a mark they were drawn as plain sent messages with no reply
        coming. Returned so the caller can redraw them.
        """
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(agent_run_id),
                    MessageModel.queue_state != QueueState.WAITING_ON_ANSWER,
                )
                .values(queue_state=QueueState.WAITING_ON_ANSWER)
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return in_order(rows)

    async def take_queued_messages_to_steer(
        self, agent_run_id: UUID
    ) -> list[MessageEntity]:
        """Mark this run's not-yet-sent queued messages as on their way to a host.

        Not a claim: the message stays owed until the host says it landed
        (``settle_steer``). It only stops the next check sending it again.
        """
        now = datetime.now(timezone.utc)
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(agent_run_id, as_run=True),
                    MessageModel.steer_dispatched_at.is_(None),
                )
                .values(steer_dispatched_at=now)
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
        return in_order(rows)

    async def settle_steer(
        self,
        *,
        agent_run_id: UUID,
        message_id: UUID,
        delivered: bool,
        detail: str | None,
    ) -> MessageEntity | None:
        """Record what the host said about one steer.

        Delivered is a claim by this run, exactly as the in-process capability
        makes one. Undelivered leaves the message queued -- the follow-up turn
        carries it -- and says why, which also makes it withdrawable again.

        Returns the updated message, or None when it was no longer queued: the
        person withdrew it, or the host reported twice. The host's reason for an
        undelivered steer is logged by the caller, not kept.
        """
        values: dict[str, object] = (
            {"queue_state": QueueState.STEERED, "steered_into_run_id": agent_run_id}
            if delivered
            else {"queue_state": QueueState.STEER_UNDELIVERED}
        )
        row = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    MessageModel.id == message_id,
                    *unclaimed_queued_messages(agent_run_id),
                )
                .values(**values)
                .returning(MessageModel)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def withdraw_queued_user_message(
        self, *, conversation_id: UUID, message_id: UUID
    ) -> bool:
        """Delete a queued message nobody is carrying yet.

        The whole condition is in the ``DELETE``, so a message that a run
        claimed -- or that went to a host -- a moment before is simply not
        matched rather than deleted out from under the agent reading it. See
        ``MessageState.is_withdrawable`` for the rule in words.
        """
        deleted = await self.session.execute(
            delete(MessageModel)
            .where(
                MessageModel.id == message_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
                MessageModel.queue_state.in_(UNANSWERED_QUEUE_STATES),
                or_(
                    MessageModel.steer_dispatched_at.is_(None),
                    MessageModel.queue_state == QueueState.STEER_UNDELIVERED,
                ),
            )
            .returning(MessageModel.id)
            .execution_options(synchronize_session=False)
        )
        return deleted.scalar_one_or_none() is not None

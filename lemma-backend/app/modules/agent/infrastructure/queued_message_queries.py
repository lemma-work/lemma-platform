"""Messages sent while a run was working, and who has delivered them.

The predicate every party agrees on, and the three writes the Agent Host's
steering added: marking a message as on its way to a host, recording what the
host said, and withdrawing one nobody is carrying yet. Counting and claiming
stay on ``ConversationRepository``, which the turn coordinator reaches through
its port; these have their own repository, because only the harness and the
withdraw route ask for them. All of it is about the metadata keys in
``domain/queued_messages.py``.

Every write here is one ``UPDATE ... RETURNING`` (or ``DELETE ... RETURNING``)
whose ``WHERE`` restates the state it moves the message out of. That is what
makes each transition happen at most once when two parties race for the same
message -- the in-process capability and the follow-up turn, a host reporting a
steer and the person withdrawing it -- without anything taking a lock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, or_, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.queued_messages import (
    DURING_ACTIVE_RUN,
    STEER_DISPATCHED_AT,
    STEER_UNDELIVERED,
    STEERED_INTO_RUN,
)
from app.modules.agent.domain.value_objects import MessageRole
from app.modules.agent.infrastructure.models import MessageModel


def stamp(**values: str):
    """``metadata || jsonb_build_object(...)``: add keys, keep the rest."""
    arguments: list[str] = []
    for key, value in values.items():
        arguments.extend((key, value))
    return MessageModel.message_metadata.op("||")(func.jsonb_build_object(*arguments))


def in_order(rows) -> list[MessageEntity]:
    return sorted((row.to_entity() for row in rows), key=lambda item: item.sequence)


def unclaimed_queued_messages(agent_run_id: UUID):
    """Messages that arrived after this run started and nobody has read yet.

    ``start`` stamps ``during_active_run`` on a message it appends to a run
    already in flight, because that run loaded its history before the
    message existed. ``steered_into_run`` is stamped back by whoever
    delivered it into a model request, so the same predicate answers both
    questions that matter: what to steer in next, and what is still owed an
    answer once the run ends.

    Compared as text rather than cast to boolean, because the column is free
    JSONB -- a cast would raise on a row where something else wrote a
    non-boolean under that key, and a miscount is the better failure.
    """
    return (
        MessageModel.agent_run_id == agent_run_id,
        MessageModel.role == MessageRole.USER.value,
        MessageModel.message_metadata[DURING_ACTIVE_RUN].astext == "true",
        MessageModel.message_metadata[STEERED_INTO_RUN].astext.is_(None),
    )


class QueuedMessageRepository:
    """Steering's writes on queued messages."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.session = uow.session

    async def take_queued_messages_to_steer(
        self, agent_run_id: UUID
    ) -> list[MessageEntity]:
        """Mark this run's not-yet-sent queued messages as on their way to a host.

        Not a claim: the message stays owed until the host says it landed
        (``settle_steer``). It only stops the next check sending it again.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    *unclaimed_queued_messages(agent_run_id),
                    MessageModel.message_metadata[STEER_DISPATCHED_AT].astext.is_(None),
                )
                .values(message_metadata=stamp(**{STEER_DISPATCHED_AT: now}))
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
        person withdrew it, or the host reported twice.
        """
        values = (
            {STEERED_INTO_RUN: str(agent_run_id)}
            if delivered
            else {STEER_UNDELIVERED: (detail or "undelivered")[:512]}
        )
        row = (
            await self.session.execute(
                update(MessageModel)
                .where(
                    MessageModel.id == message_id,
                    *unclaimed_queued_messages(agent_run_id),
                )
                .values(message_metadata=stamp(**values))
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
        ``domain.queued_messages.is_withdrawable`` for the rule in words.
        """
        metadata = MessageModel.message_metadata
        deleted = await self.session.execute(
            delete(MessageModel)
            .where(
                MessageModel.id == message_id,
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
                metadata[DURING_ACTIVE_RUN].astext == "true",
                metadata[STEERED_INTO_RUN].astext.is_(None),
                or_(
                    metadata[STEER_DISPATCHED_AT].astext.is_(None),
                    metadata[STEER_UNDELIVERED].astext.is_not(None),
                ),
            )
            .returning(MessageModel.id)
            .execution_options(synchronize_session=False)
        )
        return deleted.scalar_one_or_none() is not None

    async def release_claims(
        self, agent_run_id: UUID, *, message_ids: list[UUID]
    ) -> None:
        """Hand back messages a run claimed and then never sent.

        Only this run's claim is removed, so a message some other party has
        since taken is left alone.
        """
        await self.session.execute(
            update(MessageModel)
            .where(
                MessageModel.id.in_(message_ids),
                MessageModel.message_metadata[STEERED_INTO_RUN].astext
                == str(agent_run_id),
            )
            .values(
                message_metadata=MessageModel.message_metadata.op("-")(STEERED_INTO_RUN)
            )
            .execution_options(synchronize_session=False)
        )

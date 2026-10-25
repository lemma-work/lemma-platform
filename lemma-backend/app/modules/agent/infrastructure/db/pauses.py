"""Pausing tool calls, and what answers them.

A pause today is derived state: a pausing tool call (``ask_user``,
``request_approval``, a wait) with no tool return, and -- for the ones a person
decides -- no row in ``agent_approval_decisions``. These are the reads over
that pairing for one conversation, and the inbox's reads across many
(``pending_interactions``). The decision rows themselves are written by
``approvals.ApprovalDecisions``.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select

from app.modules.agent.domain.entities import Message as MessageEntity
from app.modules.agent.domain.pending_interactions import (
    PendingInteraction,
    PendingInteractionCursor,
)
from app.modules.agent.domain.value_objects import MessageKind
from app.modules.agent.infrastructure.db import pending_interactions
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    MessageModel,
)


class Pauses(UnitOfWorkRepository):
    """Which pausing calls are waiting, and on what."""

    async def tool_call_id_of_message(
        self, *, conversation_id: UUID, message_id: UUID
    ) -> str | None:
        """The tool_call_id of a TOOL_CALL message, by the message's own id.

        The approvals list returns messages, whose ``id`` is the message's; a
        decision is addressed by the call's ``tool_call_id``. This lets a
        client that sent the one it was given still reach the call.
        """
        return await self.session.scalar(
            select(MessageModel.tool_call_id).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.id == message_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
            )
        )

    async def get_tool_call(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> MessageEntity | None:
        """The pausing tool CALL for an approval, addressed by tool_call_id.

        Looked up directly (not through a message-window scan) so a long
        conversation can't push the original request_approval/ask_user call out
        of view during resume reconciliation.
        """
        result = await self.session.execute(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.tool_call_id == tool_call_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
            )
            .order_by(MessageModel.sequence.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def get_tool_return(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str,
    ) -> MessageEntity | None:
        """The synthesized tool RETURN for an approval, or None.

        This is the idempotency guard for approval resume: if a return already
        exists, the approved tool has already run and must NOT be re-executed.
        """
        result = await self.session.execute(
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.tool_call_id == tool_call_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
            )
            .order_by(MessageModel.sequence.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def unresolved_pausing_call_ids(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        pausing_tool_names: Sequence[str],
    ) -> list[str]:
        """Pausing calls in one run that nothing has answered yet.

        A call counts as answered once it has *either* a recorded approval
        decision or a persisted tool return. Approvals write the decision first
        and the return second, so the decision is what unblocks them; a wait
        has no decision at all and is answered purely by its return. The union
        lets one query serve both without either knowing about the other.

        Scoped to the run in SQL. This used to read up to 500 of the
        conversation's messages and filter them in Python, on the hot path of
        every approval click -- and past five hundred messages the run's own
        calls could fall outside the window entirely, which is a wrong answer
        rather than a slow one.
        """
        result = await self.session.execute(
            select(MessageModel.tool_call_id)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(list(pausing_tool_names)),
                MessageModel.tool_call_id.is_not(None),
                MessageModel.tool_call_id.not_in(
                    self._returned_call_ids(conversation_id)
                ),
                MessageModel.tool_call_id.not_in(
                    self._decided_call_ids(conversation_id)
                ),
            )
            .order_by(MessageModel.sequence)
        )
        return list(result.scalars())

    def _pausing_calls_stmt(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
        answered: Select[tuple[str | None]],
        limit: int | None,
    ) -> Select[tuple[MessageModel]]:
        """Pausing calls in one conversation that ``answered`` does not cover."""
        stmt = (
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(list(pausing_tool_names)),
                MessageModel.tool_call_id.is_not(None),
                MessageModel.tool_call_id.not_in(answered),
            )
            .order_by(MessageModel.sequence)
        )
        return stmt if limit is None else stmt.limit(limit)

    def _returned_call_ids(self, conversation_id: UUID) -> Select[tuple[str | None]]:
        return (
            select(MessageModel.tool_call_id)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
                MessageModel.tool_call_id.is_not(None),
            )
            .scalar_subquery()
        )

    def _decided_call_ids(self, conversation_id: UUID) -> Select[tuple[str | None]]:
        return (
            select(AgentApprovalDecisionModel.approval_id)
            .where(AgentApprovalDecisionModel.conversation_id == conversation_id)
            .scalar_subquery()
        )

    async def pausing_calls_awaiting_a_return(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
        limit: int | None = None,
    ) -> list[MessageEntity]:
        """Pausing calls whose synthesized tool RETURN has not landed yet.

        Deliberately blind to the decision row: an approved tool executes
        asynchronously, and the card stays visible until its return is durable
        so a repeated click can re-enqueue a reconciliation the worker died
        during. The decision-aware question is
        :meth:`pausing_calls_awaiting_a_decision`, and the two really are
        different -- reading one for the other reopens the bug each guards.

        In SQL for the reason ``unresolved_pausing_call_ids`` is: this used to be
        a scan of the newest 500 messages filtered in Python, and past that
        window a pending approval simply vanished from the list.
        """
        result = await self.session.execute(
            self._pausing_calls_stmt(
                conversation_id=conversation_id,
                pausing_tool_names=pausing_tool_names,
                answered=self._returned_call_ids(conversation_id),
                limit=limit,
            )
        )
        return [row.to_entity() for row in result.scalars()]

    async def pausing_calls_decided_awaiting_a_return(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
    ) -> list[MessageEntity]:
        """Pausing calls somebody decided whose return has not landed yet.

        The window an approved tool spends running in a worker. A message sent
        in it must not start a run of its own: that run's history would show
        the call unanswered, and the approved result would land behind it.
        """
        stmt = self._pausing_calls_stmt(
            conversation_id=conversation_id,
            pausing_tool_names=pausing_tool_names,
            answered=self._returned_call_ids(conversation_id),
            limit=None,
        ).where(MessageModel.tool_call_id.in_(self._decided_call_ids(conversation_id)))
        result = await self.session.execute(stmt)
        return [row.to_entity() for row in result.scalars()]

    async def pausing_call_ids_of_run(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        pausing_tool_names: Sequence[str],
    ) -> list[str]:
        """Every pausing call one run made, answered or not, oldest first."""
        result = await self.session.execute(
            select(MessageModel.tool_call_id)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(list(pausing_tool_names)),
                MessageModel.tool_call_id.is_not(None),
            )
            .order_by(MessageModel.sequence)
        )
        return [call_id for call_id in result.scalars() if call_id is not None]

    async def pausing_calls_awaiting_a_decision(
        self,
        *,
        conversation_id: UUID,
        pausing_tool_names: Sequence[str],
        limit: int | None = None,
    ) -> list[MessageEntity]:
        """Pausing calls nobody has decided yet, oldest first.

        A decision is what unblocks a pause, so this is the question behind
        "what is this conversation waiting on": which pause a typed reply routes
        to, and which one a new message supersedes. Both used to answer it from
        the newest 500 messages, which past that window reported a conversation
        as waiting on nothing -- so the card disappeared, the reply had nowhere
        to go, and the pause was never superseded either.
        """
        result = await self.session.execute(
            self._pausing_calls_stmt(
                conversation_id=conversation_id,
                pausing_tool_names=pausing_tool_names,
                answered=self._decided_call_ids(conversation_id),
                limit=limit,
            )
        )
        return [row.to_entity() for row in result.scalars()]

    async def list_pending_interactions(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        cursor: PendingInteractionCursor | None = None,
        limit: int = 50,
    ) -> tuple[list[PendingInteraction], PendingInteractionCursor | None]:
        """Every pause waiting on this person in this pod; see the module."""
        return await pending_interactions.list_pending_interactions(
            self.session, user_id=user_id, pod_id=pod_id, cursor=cursor, limit=limit
        )

    async def conversations_with_pending_interaction(
        self, conversation_ids: Sequence[UUID]
    ) -> set[UUID]:
        """Which of these conversations hold a pause waiting on a person."""
        return await pending_interactions.conversations_with_pending_interaction(
            self.session, conversation_ids
        )

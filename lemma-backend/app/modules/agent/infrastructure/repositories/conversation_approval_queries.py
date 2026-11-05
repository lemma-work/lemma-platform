"""The durable record of an approval, and the tool call it resolves.

Split from `ConversationRepository` the way `ConversationRunQueriesMixin`
already was: these five reads and writes are about one question — what did the
person decide, and which call was it about — and they are the half of the
repository that the approval reconciliation path uses on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.agent.domain.entities import (
    Message as MessageEntity,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageKind,
)
from app.modules.agent.domain.pending_interactions import (
    PendingInteraction,
    PendingInteractionCursor,
)
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories import pending_interaction_queries


class ConversationApprovalQueriesMixin:
    """Approval decisions and the tool calls they answer."""

    async def record_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
        agent_run_id: UUID | None,
        tool_name: str | None,
        decision: AgentRunApprovalDecision,
        response: JsonObject | None,
        resolved_by_user_id: UUID,
    ) -> bool:
        """Persist a decision once. Returns False if already recorded.

        The insert decides, not a preceding read. Two genuinely overlapping
        resolves both miss a SELECT, and the loser's flush then raises
        `IntegrityError` against `uq_agent_approval_decision` -- which nothing
        on this path catches, so it surfaced as a 500, its `_reconcile_resume`
        never ran, and the session was left poisoned. Worse, the caller's
        "somebody else won, adopt their decision" branch was unreachable for
        exactly the race it was written for: it only saw `False` when the SELECT
        found a row, i.e. on a *sequential* retry.

        `ON CONFLICT DO NOTHING` moves the decision into the one place the
        database can arbitrate it, and `rowcount` reports which caller won.
        """
        result = await self.session.execute(
            insert(AgentApprovalDecisionModel)
            .values(
                conversation_id=conversation_id,
                approval_id=approval_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                decision=decision.value,
                response=response or {},
                resolved_by_user_id=resolved_by_user_id,
            )
            .on_conflict_do_nothing(
                index_elements=["conversation_id", "approval_id"],
            )
        )
        await self.session.flush()
        return result.rowcount > 0

    async def claim_approval_execution(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> bool:
        """Win the right to run this approval's tool. True for exactly one caller.

        Approving a `request_approval` executes the wrapped tool with the user's
        authority, so it must happen at most once. The guard used to be a read
        of the tool return -- which is written *after* the tool runs, leaving a
        window one whole execution wide. A retried reconcile job (streaq
        requeues one cancelled inside the shutdown grace, and `xautoclaim`
        reclaims one whose worker died) walked straight through it and ran the
        command again.

        This is a conditional UPDATE, so Postgres arbitrates. The caller must
        commit before running the tool: an uncommitted claim is not a claim.
        """
        result = await self.session.execute(
            update(AgentApprovalDecisionModel)
            .where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
                AgentApprovalDecisionModel.execution_claimed_at.is_(None),
            )
            # The database's clock, not this process's. A claim compared
            # against a server whose clock has drifted is not a claim.
            .values(execution_claimed_at=func.now())
            .returning(AgentApprovalDecisionModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def approval_execution_claimed(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> bool:
        """Whether an executor ever took this approval's one-shot claim."""
        result = await self.session.execute(
            select(AgentApprovalDecisionModel.id).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
                AgentApprovalDecisionModel.execution_claimed_at.is_not(None),
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_approval_decision(
        self,
        *,
        conversation_id: UUID,
        approval_id: str,
    ) -> tuple[AgentRunApprovalDecision, JsonObject] | None:
        result = await self.session.execute(
            select(AgentApprovalDecisionModel).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        response = row.response if isinstance(row.response, dict) else {}
        return AgentRunApprovalDecision(row.decision), response

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
        return await pending_interaction_queries.list_pending_interactions(
            self.session, user_id=user_id, pod_id=pod_id, cursor=cursor, limit=limit
        )

    async def conversations_with_pending_interaction(
        self, conversation_ids: Sequence[UUID]
    ) -> set[UUID]:
        """Which of these conversations hold a pause waiting on a person."""
        return await pending_interaction_queries.conversations_with_pending_interaction(
            self.session, conversation_ids
        )

    async def list_resolved_approval_ids(
        self,
        *,
        conversation_id: UUID,
    ) -> set[str]:
        result = await self.session.execute(
            select(AgentApprovalDecisionModel.approval_id).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id
            )
        )
        return set(result.scalars())

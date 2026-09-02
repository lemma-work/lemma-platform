"""The durable record of an approval, and the tool call it resolves.

Split from `ConversationRepository` the way `ConversationRunQueriesMixin`
already was: these five reads and writes are about one question — what did the
person decide, and which call was it about — and they are the half of the
repository that the approval reconciliation path uses on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.agent.domain.entities import (
    Message as MessageEntity,
)
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
    MessageKind,
)
from app.modules.agent.infrastructure.models import (
    AgentApprovalDecisionModel,
    MessageModel,
)


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
        and the return second, so the decision is what unblocks them; a snooze
        has no decision at all and is answered purely by its return. The union
        lets one query serve both without either knowing about the other.

        Scoped to the run in SQL. This used to read up to 500 of the
        conversation's messages and filter them in Python, on the hot path of
        every approval click -- and past five hundred messages the run's own
        calls could fall outside the window entirely, which is a wrong answer
        rather than a slow one.
        """
        returns = (
            select(MessageModel.tool_call_id)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.kind == MessageKind.TOOL_RETURN.value,
                MessageModel.tool_call_id.is_not(None),
            )
            .scalar_subquery()
        )
        decisions = (
            select(AgentApprovalDecisionModel.approval_id)
            .where(AgentApprovalDecisionModel.conversation_id == conversation_id)
            .scalar_subquery()
        )
        result = await self.session.execute(
            select(MessageModel.tool_call_id)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.agent_run_id == agent_run_id,
                MessageModel.kind == MessageKind.TOOL_CALL.value,
                MessageModel.tool_name.in_(list(pausing_tool_names)),
                MessageModel.tool_call_id.is_not(None),
                MessageModel.tool_call_id.not_in(returns),
                MessageModel.tool_call_id.not_in(decisions),
            )
            .order_by(MessageModel.sequence)
        )
        return list(result.scalars())

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

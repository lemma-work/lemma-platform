"""The durable record of an approval: what the person decided, and who ran it.

One row per pausing call a person decided (``agent_approval_decisions``). The
insert decides a race between two resolves, and a conditional update hands the
right to execute an approved tool to exactly one caller.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
)
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models import AgentApprovalDecisionModel


class ApprovalDecisions(UnitOfWorkRepository):
    """Recording a decision once, and claiming its execution once."""

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

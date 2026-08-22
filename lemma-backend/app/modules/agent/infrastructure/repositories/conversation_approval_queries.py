"""The durable record of an approval, and the tool call it resolves.

Split from `ConversationRepository` the way `ConversationRunQueriesMixin`
already was: these five reads and writes are about one question — what did the
person decide, and which call was it about — and they are the half of the
repository that the approval reconciliation path uses on its own.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

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
        """Persist a decision once. Returns False if already recorded."""
        existing = await self.session.execute(
            select(AgentApprovalDecisionModel.id).where(
                AgentApprovalDecisionModel.conversation_id == conversation_id,
                AgentApprovalDecisionModel.approval_id == approval_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False
        self.session.add(
            AgentApprovalDecisionModel(
                conversation_id=conversation_id,
                approval_id=approval_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                decision=decision.value,
                response=response or {},
                resolved_by_user_id=resolved_by_user_id,
            )
        )
        await self.session.flush()
        return True

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
        return {row for row in result.scalars()}

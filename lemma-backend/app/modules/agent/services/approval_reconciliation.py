"""Small helpers for durable user-approval reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
    to_json_value,
)
from app.modules.agent.tools.context import BaseAgentContext

_PAUSING_TOOL_NAMES = ("ask_user", "request_approval")


def approval_reconcile_job_id(conversation_id: UUID, approval_id: str) -> str:
    """Return the deterministic worker job id for one approval decision."""
    return f"agent-approval:{conversation_id}:{approval_id}"


def pending_user_approval_messages(messages: Sequence[Message]) -> list[Message]:
    """Keep an approval visible until its synthesized return is durable."""
    returned_ids = {
        message.tool_call_id
        for message in messages
        if message.kind == MessageKind.TOOL_RETURN
        and message.tool_call_id is not None
    }
    return [
        message
        for message in messages
        if message.kind == MessageKind.TOOL_CALL
        and message.tool_name in _PAUSING_TOOL_NAMES
        and message.tool_call_id not in returned_ids
    ]


def should_defer_approved_tool(
    *,
    defer_reconciliation: bool,
    kind: str,
    decision: AgentRunApprovalDecision,
    has_tool_return: bool,
) -> bool:
    return (
        defer_reconciliation
        and kind == "request_approval"
        and decision != AgentRunApprovalDecision.DENY
        and not has_tool_return
    )


async def execute_approved_tool_as_user(
    *,
    uow: SqlAlchemyUnitOfWork,
    deps: BaseAgentContext,
    tool_name: str,
    args: dict[str, object],
) -> dict[str, object]:
    """Run an approved tool outside the database transaction; never raise."""
    # Lazy to avoid importing the tool registry back through ConversationService.
    from app.modules.agent.tools.approval.executor import ApprovalExecutor

    try:
        # Approved commands may run for minutes. Release the checked-out DB
        # connection before crossing that external boundary, then let the
        # repository session acquire a fresh one when reconciliation continues.
        await uow.commit()
        executor = ApprovalExecutor(SessionUnitOfWorkFactory(async_session_maker))
        result = await executor.execute_as_user(
            deps=deps,
            tool_name=tool_name,
            args=args,
        )
        value = to_json_value(result)
        if isinstance(value, dict) and value.get("success") is False:
            error = value.get("error") or value.get("message")
            return {
                "ok": False,
                "error": str(error or f"{tool_name} did not complete"),
            }
        return {"ok": True, "value": value}
    except Exception as exc:  # noqa: BLE001 - reported to the model, not fatal
        return {"ok": False, "error": str(exc)}

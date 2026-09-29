"""Executes an approved tool call with the *user's* authority instead of the agent's.

The agent restates the exact tool + args it wants run in ``request_approval``.
On approval we dispatch that same tool, with exactly those args, through the
shared ``AgentToolDispatcher`` under a context that carries an explicit
``ApprovedExecution``: in-process tools (pod data, connectors, subagents) build
the approver's user context from it (``tool_authorization_context``), and the
agent workload identity is stripped so sandbox tools (exec_command,
execute_python, ...) run in a workspace session minted with the user's token.
Same conversation/pod/cwd as the original run.

The approver is the conversation's owner -- the person the agent works for. A
decision from anyone else is refused before it is recorded
(``ApprovalCoordinator``), so it never reaches here.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.context import ApprovedExecution
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher
from app.modules.agent.tools.tool_errors import (
    format_tool_error,
    is_control_flow_exception,
)

logger = get_logger(__name__)

REQUEST_APPROVAL_TOOL_NAME = "request_approval"


class ApprovalExecutor:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory
        self.dispatcher = AgentToolDispatcher(uow_factory)

    async def execute_as_user(
        self,
        *,
        deps: BaseAgentContext,
        tool_name: str,
        args: dict | None,
        approval_id: str | None = None,
    ) -> object:
        if tool_name == REQUEST_APPROVAL_TOOL_NAME:
            raise ValueError("request_approval cannot approve itself")

        async with self.uow_factory() as uow:
            conversation = await ConversationRepository(uow).get_conversation(
                deps.conversation_id,
                include_runs=False,
            )
            agent = None
            if conversation is not None and conversation.agent_id is not None:
                agent = await AgentRepository(uow).get(conversation.agent_id)

        approved = ApprovedExecution(
            # The run's user is the conversation's owner: the person the agent
            # works for, and the only one whose decision is recorded.
            approver_user_id=deps.user_id,
            agent_id=deps.workload_id,
            conversation_id=deps.conversation_id,
            tool_name=tool_name,
            approval_id=approval_id,
        )
        # The approval is explicit on the context; in-process tools read it via
        # `tool_authorization_context`. The workload identity is stripped as
        # well, so the workspace session token is minted for the user.
        user_ctx = deps.model_copy(
            update={
                "approved_execution": approved,
                "workload_type": None,
                "workload_id": None,
                "agent_name": None,
            }
        )

        try:
            result = await self.dispatcher.call_tool(
                agent=agent,
                conversation=conversation,
                ctx=user_ctx,
                name=tool_name,
                arguments=args or {},
                agent_run_id=deps.agent_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - graceful tool-error boundary
            if is_control_flow_exception(exc):
                raise
            # An approved tool that fails should report the error back to the run,
            # not crash the approval task.
            logger.warning(
                "agent.executor.approved_tool_r_returning_result.degraded",
                exc_info=True,
            )
            _audit(approved, outcome="failed")
            return format_tool_error(tool_name, exc)
        _audit(approved, outcome=_outcome(result))
        return result


def _outcome(result: object) -> str:
    """``failed`` for a tool that reported failure in its result, else ``succeeded``."""
    success = (
        result.get("success")
        if isinstance(result, dict)
        else getattr(result, "success", None)
    )
    return "failed" if success is False else "succeeded"


def _audit(approved: ApprovedExecution, *, outcome: str) -> None:
    """Record that a person's authority was used, and for what.

    The one trace of an elevated action: who lent their authority, to which
    agent, for which tool, and what came of it. Info, because it is the normal
    path -- and never the arguments, which may carry secrets.
    """
    logger.info(
        "agent.approval.executed",
        # Three identifiers, the logging contract's limit: who lent authority,
        # to which agent, where. The approval's own id is in that
        # conversation's tool return.
        conversation_id=str(approved.conversation_id),
        agent_id=str(approved.agent_id) if approved.agent_id else "",
        user_id=str(approved.approver_user_id),
        tool_name=approved.tool_name,
        outcome=outcome,
    )

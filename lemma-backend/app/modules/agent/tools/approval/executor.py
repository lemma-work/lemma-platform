"""Executes an approved tool call with the *user's* authority instead of the agent's.

The agent restates the exact tool + args it wants run in ``request_approval``.
On approval we dispatch that same tool through the shared
``AgentToolDispatcher`` but under a context stripped of the agent workload
identity — so sandbox tools (exec_command/execute_python/…) run in a workspace
session minted with the user's token, and in-process tools (pod ops, etc.)
authorize as the user. Same conversation/pod/cwd as the original run.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
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

        # Strip the agent workload identity so downstream authorization (and the
        # workspace session token) resolve to the user, not the agent.
        user_ctx = deps.model_copy(
            update={
                "workload_type": None,
                "workload_id": None,
                "agent_name": None,
            }
        )

        try:
            return await self.dispatcher.call_tool(
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
            logger.debug(
                'agent.executor.approved_tool_r_returning_result.diagnostic',
                exc_info=True,
            )
            return format_tool_error(tool_name, exc)

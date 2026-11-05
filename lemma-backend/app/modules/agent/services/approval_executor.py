"""Runs a call a person approved after its run paused: ``ApprovalExecutor``.

The run is over by the time the approval lands, so its tools are rebuilt from
the conversation -- the same catalog, served for an approved re-run -- and the
call goes through them (``tools/approval/executor.run_approved``) as the
agent, lent exactly what the card showed.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.outsiders import Audience, OutsiderRunRefused
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.infrastructure.tools.approval.executor import (
    NOTHING_RUNS_AS_THE_MEMBER,
    run_approved,
)
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for
from app.modules.agent.services.tool_runtime import ToolRuntime


class ApprovalExecutor:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def execute_as_user(
        self,
        *,
        deps: ToolContext,
        tool_name: str,
        args: dict | None,
        approval_id: str | None = None,
    ) -> object:
        # Nothing may run as the member in a conversation that answers
        # strangers, whatever recorded the approval.
        if deps.audience.answers_outsiders:
            raise OutsiderRunRefused(NOTHING_RUNS_AS_THE_MEMBER)
        async with self.uow_factory() as uow:
            conversation = await Conversations(uow).get_conversation(
                deps.conversation_id, include_runs=False
            )
            agent = None
            if conversation is not None and conversation.agent_id is not None:
                agent = await AgentRepository(uow).get(conversation.agent_id)
        if conversation is None:
            raise ConversationNotFoundError()
        if Audience.of(conversation).answers_outsiders:
            raise OutsiderRunRefused(NOTHING_RUNS_AS_THE_MEMBER)
        specs = await specs_for(
            agent=agent,
            conversation=conversation,
            ctx=deps,
            surface=ToolSurface.RERUN,
            uow_factory=self.uow_factory,
        )
        return await run_approved(
            ToolRuntime(specs),
            self.uow_factory,
            deps=deps,
            tool_name=tool_name,
            args=args,
            approval_id=approval_id,
        )


__all__ = ["ApprovalExecutor"]

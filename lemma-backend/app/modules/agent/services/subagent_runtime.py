"""Which runtime a sub-agent's conversation runs on.

A child runs on what its parent runs on, unless the agent it is has a runtime
of its own -- or the delegation named another model of the same runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import AgentRepository

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent.domain.context import ToolContext


class _RunReader(Protocol):
    async def get_agent_run(self, agent_run_id: UUID) -> AgentRun | None: ...


class _ConversationReader(Protocol):
    async def get_conversation(self, conversation_id: UUID) -> Conversation | None: ...


class _AgentReader(Protocol):
    async def get_by_pod_and_name(self, *, pod_id: UUID, name: str) -> Agent | None: ...


class ParentRuntimeLookup(Protocol):
    """Which runtime a sub-agent's conversation is pinned to, if any."""

    async def __call__(
        self,
        uow: SqlAlchemyUnitOfWork,
        deps: ToolContext,
        *,
        is_self: bool,
        target_name: str | None,
    ) -> AgentRuntimeConfig | None: ...


async def inherited_runtime(
    *,
    runs: _RunReader,
    conversations: _ConversationReader,
    agents: _AgentReader,
    deps: ToolContext,
    is_self: bool,
    target_name: str | None,
) -> AgentRuntimeConfig | None:
    """The runtime a sub-agent's conversation is pinned to, if any.

    A sub-agent runs on what its parent is running on: the model the person
    picked for the conversation, or the one its agent defaults to. Without
    this the child had no runtime of its own and fell to the pod default, so a
    conversation switched to another model -- or to a coding agent on this Mac
    -- spawned helpers that quietly ran somewhere else. A named agent
    configured with a runtime of its own keeps it: that choice was made for
    that agent on purpose.
    """
    if not is_self and target_name is not None:
        target = await agents.get_by_pod_and_name(pod_id=deps.pod_id, name=target_name)
        if target is not None and target.agent_runtime is not None:
            return None
    if deps.agent_run_id is not None:
        run = await runs.get_agent_run(deps.agent_run_id)
        if run is not None and run.agent_runtime is not None:
            return run.agent_runtime
    parent = await conversations.get_conversation(deps.conversation_id)
    return parent.agent_runtime if parent is not None else None


async def parent_runtime_from(
    uow: SqlAlchemyUnitOfWork,
    deps: ToolContext,
    *,
    is_self: bool,
    target_name: str | None,
) -> AgentRuntimeConfig | None:
    return await inherited_runtime(
        runs=Runs(uow),
        conversations=Conversations(uow),
        agents=AgentRepository(uow),
        deps=deps,
        is_self=is_self,
        target_name=target_name,
    )


__all__ = ["ParentRuntimeLookup", "inherited_runtime", "parent_runtime_from"]

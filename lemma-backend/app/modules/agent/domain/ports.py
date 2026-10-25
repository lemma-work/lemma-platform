"""Ports for agent module boundaries."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, AsyncIterator, Protocol, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import (
    Agent,
    Conversation,
    Message,
)
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    HarnessKind,
)

if TYPE_CHECKING:
    from app.modules.agent.domain.background import BackgroundAnswer, BackgroundCall


class Harness(Protocol):
    """Runtime adapter that hides the underlying agent framework."""

    kind: HarnessKind

    # Not `async def`: that would declare a coroutine *returning* an iterator,
    # which is not what either harness is. Both are async generator functions and
    # every caller iterates the return value directly, un-awaited.
    def run[DepsT: AgentContext](
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        messages: Sequence[Message],
        ctx: DepsT,
        options: HarnessOptions[DepsT],
        agent_run_id: UUID,
    ) -> AsyncIterator[AgentEvent]:
        """Execute one agent run and yield normalized events."""
        ...


class ModelProvider(Protocol):
    """The vendor boundary for model calls made outside a turn (DES-08).

    A model is opaque on this side: whatever ``build`` returns is handed back
    to ``run``.
    """

    def build(
        self, resolved: ResolvedAgentRuntime, *, fallback_model_name: str | None = None
    ) -> object | None:
        """The model ``resolved`` names, or ``None`` when it cannot be built."""
        ...

    async def run(self, model: object, call: BackgroundCall) -> BackgroundAnswer:
        """Ask ``model`` one background call."""
        ...


class LiveEvents(Protocol):
    """The transport boundary for a conversation's live events.

    One publish feeds every transport (SSE, the live socket, a visitor's page);
    a fake is a list.
    """

    async def publish(self, conversation_id: UUID, event: dict[str, object]) -> None:
        """Publish one frame (``domain/live_events``), best effort."""
        ...


class AgentRepository(Protocol):
    async def get(self, agent_id: UUID, ctx: Context | None = None) -> Agent | None: ...

    async def get_many(self, agent_ids: Collection[UUID]) -> dict[UUID, Agent]: ...

    async def get_by_pod_and_name(
        self, *, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> Agent | None: ...

    async def create(self, agent: Agent) -> Agent: ...

    #: The pod's own assistant, minted with the pod. Separate from `create`
    #: because nobody created it: it emits no agent-created event, takes no
    #: entity, and fills in only identity -- what it does comes from the
    #: `POD_DEFAULT` constants at run time, not from its row.
    async def create_pod_default(self, *, pod_id: UUID, user_id: UUID) -> Agent: ...

    async def update(self, agent: Agent) -> Agent: ...

    async def delete(self, agent_id: UUID) -> None: ...

    async def list_by_pod(
        self,
        *,
        pod_id: UUID,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[Agent], UUID | None]: ...

    async def list_visible_by_pod(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[Agent], UUID | None]: ...

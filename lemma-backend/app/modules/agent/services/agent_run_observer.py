"""Optional observer contract for agent-run lifecycle events."""

from typing import Protocol

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentEvent
from app.modules.agent.tools.context import ConversationContext


class AgentRunObserver(Protocol):
    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

    async def on_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

    async def on_run_finished(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None: ...

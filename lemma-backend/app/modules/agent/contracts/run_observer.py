"""What a run tells the delivery surface watching it.

A contract rather than a runner-internal protocol because the implementation
lives in another module: the surface layer streams a run onto Slack, Teams or
email through one of these, and the runner only ever calls the four methods.
Written down here so a rename on either side fails the type check instead of a
delivery in production.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentEvent
from app.modules.agent.domain.context import ToolContext


class RunObserver(Protocol):
    """Receives a run's lifecycle as it happens. Failures never fail the run."""

    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ToolContext,
    ) -> None: ...

    async def on_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
        ctx: ToolContext,
    ) -> None: ...

    async def on_run_finished(
        self,
        conversation: Conversation,
        ctx: ToolContext,
    ) -> None: ...

    async def on_run_failed(
        self,
        conversation: Conversation,
        error: Exception,
    ) -> None: ...

"""Bounded execution and streaming for model tool-call batches."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from app.modules.agent.domain.run_limits import MAX_PARALLEL_TOOL_EXECUTIONS
from app.modules.agent.domain.value_objects import AgentEvent, AgentEventType

StopChecker = Callable[[], Awaitable[bool]]


class BoundedToolExecutionCapability(AbstractCapability[object]):
    """Queue tool bodies through a small shared concurrency pool.

    Every model-requested call still executes and returns its real result. The
    bound protects AgentBox, subprocess, and connector capacity without turning
    a recoverable model response into rejected tool calls or a failed agent run.
    """

    def __init__(
        self,
        *,
        max_parallel_executions: int = MAX_PARALLEL_TOOL_EXECUTIONS,
    ) -> None:
        self._execution_slots = asyncio.Semaphore(max(1, max_parallel_executions))

    async def wrap_tool_execute(
        self,
        ctx: RunContext[object],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        handler,
    ) -> Any:
        del ctx, call, tool_def
        async with self._execution_slots:
            return await handler(args)


async def release_tool_call_batch(
    *,
    token_chunks: Sequence[dict[str, str]],
    call_events: Sequence[AgentEvent],
    agent_run_id: UUID,
    should_stop: StopChecker | None,
) -> AsyncIterator[AgentEvent]:
    for token_chunk in token_chunks:
        yield AgentEvent(
            type=AgentEventType.TOKEN,
            data=token_chunk,
            agent_run_id=agent_run_id,
        )
        if await _should_stop(should_stop):
            yield _stopped_event(agent_run_id)
            return

    for event in call_events:
        yield event
        if await _should_stop(should_stop):
            yield _stopped_event(agent_run_id)
            return


async def _should_stop(should_stop: StopChecker | None) -> bool:
    return should_stop is not None and await should_stop()


def _stopped_event(agent_run_id: UUID) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.STOPPED,
        data={"reason": "stop_requested"},
        agent_run_id=agent_run_id,
    )

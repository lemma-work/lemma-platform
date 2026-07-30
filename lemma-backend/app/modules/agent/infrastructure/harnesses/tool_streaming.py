"""Cancellation-aware streaming for in-process tool execution."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import AsyncIterator
from uuid import UUID

from pydantic_ai import BinaryContent, FunctionToolCallEvent, FunctionToolResultEvent

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    to_json_value,
)

logger = get_logger(__name__)
StopChecker = Callable[[], Awaitable[bool]]
TOOL_STOP_POLL_SECONDS = 0.25


async def stream_tool_results(
    *,
    node,
    run_ctx,
    agent_run_id: UUID,
    malformed_tool_call_ids: set[str],
    emitted_tool_response_ids: set[str],
    should_stop: StopChecker | None,
) -> AsyncIterator[AgentEvent]:
    async with node.stream(run_ctx) as handle_stream:
        iterator = handle_stream.__aiter__()
        next_event_task: asyncio.Task[object] | None = None
        try:
            while True:
                if await _should_stop(should_stop):
                    yield _stopped_event(agent_run_id)
                    return

                next_event_task = asyncio.create_task(anext(iterator))
                while not next_event_task.done():
                    await asyncio.wait(
                        {next_event_task},
                        timeout=TOOL_STOP_POLL_SECONDS,
                    )
                    if not next_event_task.done() and await _should_stop(should_stop):
                        next_event_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await next_event_task
                        next_event_task = None
                        yield _stopped_event(agent_run_id)
                        return

                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    next_event_task = None

                async for agent_event in _tool_result_events(
                    event=event,
                    agent_run_id=agent_run_id,
                    malformed_tool_call_ids=malformed_tool_call_ids,
                    emitted_tool_response_ids=emitted_tool_response_ids,
                ):
                    yield agent_event
        finally:
            if next_event_task is not None and not next_event_task.done():
                next_event_task.cancel()
                with suppress(asyncio.CancelledError):
                    await next_event_task


async def _tool_result_events(
    *,
    event: object,
    agent_run_id: UUID,
    malformed_tool_call_ids: set[str],
    emitted_tool_response_ids: set[str],
) -> AsyncIterator[AgentEvent]:
    if isinstance(event, FunctionToolCallEvent):
        return
    if not isinstance(event, FunctionToolResultEvent):
        return
    result_part = event.part
    if result_part.tool_call_id in malformed_tool_call_ids:
        logger.debug(
            "agent.pydantic_ai.skipping_tool_result_malformed_call.diagnostic",
            tool_call_id=result_part.tool_call_id,
        )
        return
    tool_output = result_part.content
    if isinstance(tool_output, BinaryContent):
        tool_output = {
            "type": "binary_content",
            "media_type": tool_output.media_type,
            "size_bytes": len(tool_output.data) if tool_output.data else 0,
        }
    else:
        tool_output = to_json_value(tool_output)

    emitted_tool_response_ids.add(result_part.tool_call_id)
    yield AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name=result_part.tool_name or "unknown_tool",
            tool_call_id=result_part.tool_call_id,
            tool_result=tool_output,
            metadata={"tool_name": result_part.tool_name or "unknown_tool"},
        ),
        agent_run_id=agent_run_id,
    )


async def _should_stop(should_stop: StopChecker | None) -> bool:
    return should_stop is not None and await should_stop()


def _stopped_event(agent_run_id: UUID) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.STOPPED,
        data={"reason": "stop_requested"},
        agent_run_id=agent_run_id,
    )

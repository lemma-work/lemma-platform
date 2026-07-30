"""Admission control for one in-process model tool-call response."""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import AsyncIterator
from uuid import UUID

from pydantic_ai.exceptions import UsageLimitExceeded

from app.core.log.log import get_logger
from app.modules.agent.domain.run_limits import (
    MAX_AGENT_TOOL_CALLS_PER_RESPONSE,
    MAX_IDENTICAL_TOOL_CALLS_PER_RESPONSE,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
)

logger = get_logger(__name__)
StopChecker = Callable[[], Awaitable[bool]]


def validate_tool_call_batch(events: Sequence[AgentEvent]) -> None:
    if len(events) > MAX_AGENT_TOOL_CALLS_PER_RESPONSE:
        logger.warning(
            "agent.pydantic_ai.tool_batch_limit_exceeded.degraded",
            tool_call_count=len(events),
            tool_call_limit=MAX_AGENT_TOOL_CALLS_PER_RESPONSE,
        )
        raise UsageLimitExceeded(
            "A single model response exceeded the tool call safety limit."
        )

    repeated_call_count = _max_identical_tool_call_count(events)
    if repeated_call_count > MAX_IDENTICAL_TOOL_CALLS_PER_RESPONSE:
        logger.warning(
            "agent.pydantic_ai.repeated_tool_batch_rejected.degraded",
            repeated_tool_call_count=repeated_call_count,
            repeated_tool_call_limit=MAX_IDENTICAL_TOOL_CALLS_PER_RESPONSE,
        )
        raise UsageLimitExceeded(
            "A single model response repeated an identical tool call too many times."
        )


async def release_tool_call_batch(
    *,
    token_chunks: Sequence[dict[str, str]],
    call_events: Sequence[AgentEvent],
    agent_run_id: UUID,
    should_stop: StopChecker | None,
) -> AsyncIterator[AgentEvent]:
    validate_tool_call_batch(call_events)
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


def _max_identical_tool_call_count(events: Sequence[AgentEvent]) -> int:
    fingerprints: dict[str, int] = {}
    maximum = 0
    for event in events:
        message = event.data
        fingerprint = json.dumps(
            [
                getattr(message, "tool_name", None),
                getattr(message, "tool_args", None),
            ],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        count = fingerprints.get(fingerprint, 0) + 1
        fingerprints[fingerprint] = count
        maximum = max(maximum, count)
    return maximum


async def _should_stop(should_stop: StopChecker | None) -> bool:
    return should_stop is not None and await should_stop()


def _stopped_event(agent_run_id: UUID) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.STOPPED,
        data={"reason": "stop_requested"},
        agent_run_id=agent_run_id,
    )

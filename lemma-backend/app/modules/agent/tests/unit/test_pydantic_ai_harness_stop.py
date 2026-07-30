from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import anyio
import pytest
from pydantic_ai.messages import (
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolCallPart,
)

from app.modules.agent.domain.value_objects import AgentEventType, MessageKind
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.tool_batch import (
    BoundedToolExecutionCapability,
)
from app.modules.agent.tools.tool_errors import AgentInputRequired


class _ScopedRequestStream:
    def __init__(self, events=None) -> None:
        self._events = iter(
            events
            or [
                PartStartEvent(index=0, part=TextPart("hello world")),
                PartEndEvent(index=0, part=TextPart("hello world")),
            ]
        )
        self._scope = None

    async def __aenter__(self):
        self._scope = anyio.move_on_after(10)
        self._scope.__enter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        assert self._scope is not None
        return self._scope.__exit__(exc_type, exc, tb)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration


class _Node:
    def __init__(self, events=None) -> None:
        self.events = events

    def stream(self, ctx):
        return _ScopedRequestStream(self.events)


class _Run:
    ctx = object()


class _HangingToolStream:
    def __init__(self) -> None:
        self.cancelled = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            await anyio.sleep_forever()
        finally:
            self.cancelled = True


class _HangingToolNode:
    def __init__(self) -> None:
        self.stream_instance = _HangingToolStream()

    def stream(self, ctx):
        return self.stream_instance


@pytest.mark.asyncio
async def test_bounded_tool_execution_queues_parallel_side_effects() -> None:
    capability = BoundedToolExecutionCapability(max_parallel_executions=5)
    active = 0
    maximum_active = 0

    async def invoke(index: int) -> None:
        async def handler(args):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return args

        await capability.wrap_tool_execute(
            SimpleNamespace(),
            call=ToolCallPart(
                tool_name="exec_command",
                args={"cmd": f"echo {index}"},
                tool_call_id=f"parallel-{index}",
            ),
            tool_def=SimpleNamespace(),
            args={"cmd": f"echo {index}"},
            handler=handler,
        )

    await asyncio.gather(*(invoke(index) for index in range(12)))

    assert maximum_active == 5


@pytest.mark.asyncio
async def test_stream_stop_unwinds_anyio_cancel_scope_in_generator_task() -> None:
    harness = PydanticAIHarness()
    should_stop = False
    events = []

    async def stop_requested() -> bool:
        return should_stop

    with anyio.move_on_after(10, shield=True):
        async for event in harness._stream_model_request(
            _Node(),
            _Run(),
            agent_run_id=UUID("00000000-0000-0000-0000-000000000001"),
            malformed_tool_call_ids=set(),
            should_stop=stop_requested,
        ):
            events.append(event)
            should_stop = True

    assert [event.type for event in events] == [
        AgentEventType.TOKEN,
        AgentEventType.STOPPED,
    ]


@pytest.mark.asyncio
async def test_large_tool_batch_streams_every_call_without_rejection() -> None:
    harness = PydanticAIHarness()
    stream_events = []
    for index in range(12):
        part = ToolCallPart(
            tool_name="exec_command",
            args={"cmd": f"echo {index}"},
            tool_call_id=f"call-{index}",
        )
        stream_events.extend(
            [
                PartStartEvent(index=index, part=part),
                PartEndEvent(index=index, part=part),
            ]
        )
    events = []

    async for event in harness._stream_model_request(
        _Node(stream_events),
        _Run(),
        agent_run_id=UUID("00000000-0000-0000-0000-000000000002"),
        malformed_tool_call_ids=set(),
        should_stop=None,
    ):
        events.append(event)

    tool_calls = [
        event
        for event in events
        if event.type is AgentEventType.MESSAGE
        and getattr(event.data, "kind", None) is MessageKind.TOOL_CALL
    ]
    assert len(tool_calls) == 12
    assert [event.data.tool_call_id for event in tool_calls] == [
        f"call-{index}" for index in range(12)
    ]
    assert not any(event.type is AgentEventType.STATUS for event in events)


@pytest.mark.asyncio
async def test_repeated_tool_batch_is_not_synthetically_suppressed() -> None:
    harness = PydanticAIHarness()
    stream_events = []
    for index in range(3):
        part = ToolCallPart(
            tool_name="exec_command",
            args={"cmd": "echo done"},
            tool_call_id=f"repeated-call-{index}",
        )
        stream_events.extend(
            [
                PartStartEvent(index=index, part=part),
                PartEndEvent(index=index, part=part),
            ]
        )
    events = []

    async for event in harness._stream_model_request(
        _Node(stream_events),
        _Run(),
        agent_run_id=UUID("00000000-0000-0000-0000-000000000003"),
        malformed_tool_call_ids=set(),
        should_stop=None,
    ):
        events.append(event)

    tool_calls = [
        event
        for event in events
        if event.type is AgentEventType.MESSAGE
        and getattr(event.data, "kind", None) is MessageKind.TOOL_CALL
    ]
    assert [event.data.tool_call_id for event in tool_calls] == [
        "repeated-call-0",
        "repeated-call-1",
        "repeated-call-2",
    ]


@pytest.mark.asyncio
async def test_stop_cancels_a_hanging_tool_batch_promptly() -> None:
    harness = PydanticAIHarness()
    node = _HangingToolNode()
    stop_checks = 0

    async def stop_requested() -> bool:
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks > 1

    with anyio.fail_after(2):
        events = [
            event
            async for event in harness._stream_tool_calls(
                node,
                _Run(),
                conversation_id=UUID("00000000-0000-0000-0000-000000000004"),
                agent_run_id=UUID("00000000-0000-0000-0000-000000000005"),
                malformed_tool_call_ids=set(),
                emitted_tool_response_ids=set(),
                should_stop=stop_requested,
            )
        ]

    assert [event.type for event in events] == [AgentEventType.STOPPED]
    assert node.stream_instance.cancelled is True


@pytest.mark.asyncio
async def test_run_emits_waiting_event_when_tool_requests_input(monkeypatch) -> None:
    """A tool raising AgentInputRequired ends the run with a single WAITING event."""
    harness = PydanticAIHarness()
    conversation_id = UUID("00000000-0000-0000-0000-0000000000aa")
    agent_run_id = UUID("00000000-0000-0000-0000-0000000000bb")

    async def fake_execute(**_kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise AgentInputRequired("tool-call-1", "ask_user")

    monkeypatch.setattr(harness, "_execute", fake_execute)

    events = [
        event
        async for event in harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(id=conversation_id),
            messages=[],
            ctx=SimpleNamespace(),
            options=SimpleNamespace(should_stop=None),
            agent_run_id=agent_run_id,
        )
    ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.WAITING
    assert events[0].data["tool_call_id"] == "tool-call-1"
    assert events[0].data["kind"] == "ask_user"
    assert events[0].data["conversation_id"] == str(conversation_id)

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import anyio
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    ModelResponse,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentEventType, HarnessOptions
from app.modules.agent.infrastructure.harnesses.pydantic_ai_streaming import (
    ModelRequestStreamer,
)
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.tools.tool_errors import AgentInputRequired


class _ScopedRequestStream:
    def __init__(self) -> None:
        self._events = iter(
            [
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
    def stream(self, ctx):
        return _ScopedRequestStream()


class _Run:
    ctx = object()


@pytest.mark.asyncio
async def test_stream_stop_unwinds_anyio_cancel_scope_in_generator_task() -> None:
    should_stop = False
    events = []

    async def stop_requested() -> bool:
        return should_stop

    streamer = ModelRequestStreamer(
        emit_tokens=True,
        agent_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        should_stop=stop_requested,
    )
    with anyio.move_on_after(10, shield=True):
        async for event in streamer._stream_model_request(
            _Node(),
            _Run(),
            malformed_tool_call_ids=set(),
        ):
            events.append(event)
            should_stop = True

    # The MESSAGE is the point of `test_a_stop_keeps_the_answer_already_shown`
    # below; it is here because this list used to read TOKEN, STOPPED and that
    # missing message was a defect, not the contract. What this test is about
    # is the line above: the scope unwinds in the generator's own task.
    assert [event.type for event in events] == [
        AgentEventType.TOKEN,
        AgentEventType.MESSAGE,
        AgentEventType.STOPPED,
    ]


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


class _EmptyAgentRun:
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, requests=0, tool_calls=0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _Output(BaseModel):
    answer: str


class TestPausingToolsBesideFinalAnswer:
    """A pausing tool emitted in the same model response as the final answer.

    A TASK conversation returns through an output tool. Under pydantic-ai's
    default "early" end strategy, any normal tool in that same response is
    skipped the moment the output tool validates — so `request_approval` never
    executes, never raises AgentInputRequired, and never persists. The run
    completes and the user is simply never asked. `graceful` is what makes the
    sibling run, so these tests pin the setting to the behaviour it buys rather
    than to the string itself.
    """

    @staticmethod
    def _agent(end_strategy: str, pausing_tool_name: str):
        async def pausing_tool() -> str:
            raise AgentInputRequired("call-1", pausing_tool_name)

        pausing_tool.__name__ = pausing_tool_name

        def model_fn(messages, info: AgentInfo) -> ModelResponse:
            del messages
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=info.output_tools[0].name, args={"answer": "done"}
                    ),
                    ToolCallPart(tool_name=pausing_tool_name, args={}),
                ]
            )

        return PydanticAgent(
            FunctionModel(model_fn),
            output_type=_Output,
            toolsets=[FunctionToolset(tools=[pausing_tool])],
            end_strategy=end_strategy,
        )

    @pytest.mark.parametrize("pausing_tool_name", ["request_approval", "ask_user"])
    @pytest.mark.asyncio
    async def test_the_pause_still_happens(self, pausing_tool_name: str) -> None:
        agent = self._agent("graceful", pausing_tool_name)

        with pytest.raises(AgentInputRequired):
            await agent.run("go")

    @pytest.mark.parametrize("pausing_tool_name", ["request_approval", "ask_user"])
    @pytest.mark.asyncio
    async def test_the_early_strategy_is_what_swallowed_it(
        self, pausing_tool_name: str
    ) -> None:
        """The bug itself, pinned: with "early" the run answers as if the user
        had never been asked. If this ever stops holding, the fix above has
        become unnecessary — but until then it is the reason for it."""
        agent = self._agent("early", pausing_tool_name)

        result = await agent.run("go")

        assert result.output.answer == "done"

    @pytest.mark.asyncio
    async def test_the_harness_configures_the_graceful_strategy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The behaviour above only reaches production through this kwarg."""
        captured: dict[str, object] = {}

        class _Agent:
            def __init__(self, model, **kwargs):
                del model
                captured.update(kwargs)

            def iter(self, *args, **kwargs):
                del args, kwargs
                return _EmptyAgentRun()

        monkeypatch.setattr(harness_module, "PydanticAIAgent", _Agent)
        monkeypatch.setattr(
            harness_module, "_runtime_profile_model", lambda options: object()
        )
        pod_id = uuid4()
        conversation = Conversation(pod_id=pod_id, user_id=uuid4())

        events = [
            event
            async for event in PydanticAIHarness()._execute(
                agent=Agent(
                    pod_id=pod_id,
                    user_id=conversation.user_id,
                    name="assistant",
                    instruction="",
                ),
                conversation=conversation,
                messages=[],
                ctx=AgentContext(
                    user_id=conversation.user_id,
                    pod_id=pod_id,
                    conversation_id=conversation.id,
                ),
                options=HarnessOptions(
                    model_name="test-model",
                    history_summarization_enabled=False,
                ),
                agent_run_id=uuid4(),
                should_stop=None,
            )
        ]

        assert captured["end_strategy"] == "graceful"
        assert [event.type for event in events] == [AgentEventType.USAGE]


@pytest.mark.asyncio
async def test_a_stop_keeps_the_answer_already_shown() -> None:
    """Stop must not destroy the reply the user is reading.

    `_emit_token_chunks` ends its stream by emitting STOPPED, and the driver
    returned on that -- which landed the stop between "these tokens were shown"
    and "this part became a message". The tokens had already been streamed to
    the client, the message was never written, and the client then cleared the
    text it had been showing. Pressing Stop deleted the answer.

    The part is the atomic unit: a stop arriving inside one waits for it.
    """
    should_stop = False
    events = []

    async def stop_requested() -> bool:
        return should_stop

    streamer = ModelRequestStreamer(
        emit_tokens=True,
        agent_run_id=UUID("00000000-0000-0000-0000-000000000001"),
        should_stop=stop_requested,
    )
    with anyio.move_on_after(10, shield=True):
        async for event in streamer._stream_model_request(
            _Node(),
            _Run(),
            malformed_tool_call_ids=set(),
        ):
            events.append(event)
            should_stop = True

    kinds = [event.type for event in events]
    assert AgentEventType.MESSAGE in kinds, (
        "the streamed text was never persisted, so the stop discarded it"
    )
    message = events[kinds.index(AgentEventType.MESSAGE)]
    assert message.data.text == "hello world"
    # And the stop still ends the stream, last.
    assert kinds[-1] is AgentEventType.STOPPED

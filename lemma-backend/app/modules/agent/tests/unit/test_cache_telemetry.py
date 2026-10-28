"""Each model response says how much of its request came from the cache, and where.

The run hands pydantic-ai its place in the conversation, which the metered model
reads back for the usage record; and each response adds a span event with the
same counts, tagged with the run and the step.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.infrastructure.harness.lemma import harness as harness_module
from app.modules.agent.infrastructure.harness.lemma.cache_telemetry import (
    CACHE_EVENT,
    CacheTelemetry,
    cache_attributes,
)
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.usage.contracts.metering import RUN_INDEX_METADATA_KEY

pytestmark = pytest.mark.unit


def _response(*, input_tokens: int, read: int, write: int = 0) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("ok")],
        usage=RequestUsage(
            input_tokens=input_tokens, cache_read_tokens=read, cache_write_tokens=write
        ),
    )


def test_the_attributes_carry_the_counts_the_ratio_and_the_position() -> None:
    attributes = cache_attributes(
        _response(input_tokens=2000, read=1500, write=100), run_index=2, step_index=1
    )

    assert attributes == {
        "lemma.cache.input_tokens": 2000,
        "lemma.cache.read_tokens": 1500,
        "lemma.cache.write_tokens": 100,
        "lemma.cache.hit_ratio": 0.75,
        "lemma.run.step_index": 1,
        "lemma.run.index": 2,
    }
    # OTel takes no None: an unknown run and an empty request leave theirs out.
    assert "lemma.run.index" not in cache_attributes(
        _response(input_tokens=0, read=0), run_index=None, step_index=1
    )


@pytest.mark.anyio
async def test_each_response_adds_an_event_on_the_current_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)
    ctx = RunContext(
        deps=None,
        model=FunctionModel(lambda messages, info: None),  # type: ignore[arg-type, return-value]
        usage=RunUsage(),
        run_step=3,
    )

    with tracer.start_as_current_span("chat"):
        await CacheTelemetry(run_index=5).after_model_request(
            ctx,
            request_context=None,  # type: ignore[arg-type]
            response=_response(input_tokens=4000, read=3000),
        )

    (span,) = exporter.get_finished_spans()
    (event,) = span.events
    assert event.name == CACHE_EVENT
    assert event.attributes is not None
    assert event.attributes["lemma.run.index"] == 5
    assert event.attributes["lemma.run.step_index"] == 3
    assert event.attributes["lemma.cache.hit_ratio"] == 0.75


@pytest.mark.anyio
async def test_the_run_tells_pydantic_ai_its_place_in_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def answer(messages: object, info: AgentInfo) -> AsyncIterator[str]:
        yield "done"

    monkeypatch.setattr(
        harness_module,
        "_runtime_profile_model",
        lambda options: FunctionModel(stream_function=answer),
    )
    original_iter = harness_module.PydanticAIAgent.iter

    def capturing_iter(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        captured.update(kwargs)
        return original_iter(self, *args, **kwargs)

    monkeypatch.setattr(harness_module.PydanticAIAgent, "iter", capturing_iter)
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    async for _ in PydanticAIHarness()._execute(
        agent=Agent(
            pod_id=conversation.pod_id,
            user_id=conversation.user_id,
            name="assistant",
            instruction="",
        ),
        conversation=conversation,
        messages=[],
        ctx=ToolContext(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            conversation_id=conversation.id,
        ),
        options=HarnessOptions(
            model_name="test-model",
            history_summarization_enabled=False,
            run_index=7,
        ),
        agent_run_id=uuid4(),
        should_stop=None,
    ):
        pass

    assert captured["metadata"] == {RUN_INDEX_METADATA_KEY: 7}

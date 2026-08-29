"""Tokens must leave the harness while the model is still producing them.

The E2E suite can prove tokens reach the client in the right order, but not that
they arrive *early* — a mock model answers in microseconds, so "streamed" and
"buffered until the run ended" finish at the same instant there. Here the model
is deliberately slow, which makes the difference measurable: a harness that
accumulated the answer and released it at the end would show every token landing
in one burst at the end of the run.

That is the failure the UI reports as "I hit send and nothing happened until I
refreshed", so it is worth a test that can actually see it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentEventType, HarnessOptions
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness

pytestmark = pytest.mark.unit

_ANSWER = (
    "Ashwin retired holding 537 Test wickets from 106 matches. He reached 500 "
    "in 98 Tests and took 37 five-wicket hauls along the way, a record second "
    "only to Muralitharan among spinners in the modern era."
)

# Small enough that the answer crosses `CharStreamBuffer`'s 50-char window
# several times, so a working harness emits a sequence of TOKEN events.
_DELTA_CHARS = 8
_DELTA_GAP_SECONDS = 0.01


def _slow_stream(text: str):
    """A model that trickles its answer out, the way a real provider does."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        for start in range(0, len(text), _DELTA_CHARS):
            await asyncio.sleep(_DELTA_GAP_SECONDS)
            yield text[start : start + _DELTA_CHARS]

    return stream_fn


async def _timed_events(model, *, monkeypatch) -> list[tuple[float, AgentEventType]]:
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda options: model)
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    loop = asyncio.get_running_loop()
    started = loop.time()
    timeline: list[tuple[float, AgentEventType]] = []
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
    ):
        timeline.append((loop.time() - started, event.type))
    return timeline


@pytest.mark.asyncio
async def test_tokens_are_emitted_across_the_run_not_in_one_burst(
    monkeypatch,
) -> None:
    timeline = await _timed_events(
        FunctionModel(stream_function=_slow_stream(_ANSWER)), monkeypatch=monkeypatch
    )

    token_times = [at for at, kind in timeline if kind is AgentEventType.TOKEN]
    assert len(token_times) > 1, timeline

    total = timeline[-1][0]
    spread = token_times[-1] - token_times[0]
    # Most of the run should be spent streaming. Buffering would collapse the
    # spread toward zero while leaving `total` unchanged.
    assert spread > total / 2, (
        f"tokens spanned only {spread:.3f}s of a {total:.3f}s run -- they were "
        "released in a burst rather than streamed"
    )
    # And the first one is early, which is what stops the UI looking hung.
    assert token_times[0] < total / 2, timeline


@pytest.mark.asyncio
async def test_the_durable_message_lands_only_once_the_response_is_whole(
    monkeypatch,
) -> None:
    """MESSAGE is held to the end of the model response, TOKEN is not.

    Both halves matter: holding the durable write is what makes a stream-drop
    retry safe (a half-written response must not be persisted), and *not*
    holding the tokens is what keeps the UI live while that protection is in
    place. A change that buffered both would still pass an ordering-only test.
    """
    timeline = await _timed_events(
        FunctionModel(stream_function=_slow_stream(_ANSWER)), monkeypatch=monkeypatch
    )

    messages = [at for at, kind in timeline if kind is AgentEventType.MESSAGE]
    tokens = [at for at, kind in timeline if kind is AgentEventType.TOKEN]

    assert len(messages) == 1, timeline
    assert messages[0] >= tokens[-1], timeline

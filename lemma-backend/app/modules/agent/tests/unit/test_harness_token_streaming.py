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
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harness.lemma import harness as harness_module
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harness.lemma.streaming import (
    DEFAULT_TOKEN_FLUSH_SECONDS,
)

#: The reading-pace promise, written down apart from the setting that keeps it,
#: so a longer interval fails here instead of lowering what this test expects.
_FLUSH_CONTRACT_SECONDS = 0.05

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


def _slow_stream(
    text: str, *, chars: int = _DELTA_CHARS, gap: float = _DELTA_GAP_SECONDS
):
    """A model that trickles its answer out, the way a real provider does."""

    async def stream_fn(messages, info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        for start in range(0, len(text), chars):
            await asyncio.sleep(gap)
            yield text[start : start + chars]

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

    # Measured against the model's own production window, not against the run.
    #
    # This used to assert `spread > total / 2`, where `total` is the timestamp
    # of the run's *last* event. Everything after the final token -- run
    # finalization, the durable MESSAGE write -- counts toward `total` and can
    # never count toward `spread`, so the assertion really said "finalization
    # must be quicker than the model", which is a claim about how fast the
    # machine is. It failed on an idle developer laptop and intermittently on
    # CI with a perfectly healthy stream: 0.268s of tokens, 0.43s of tail.
    #
    # `_slow_stream` sleeps `_DELTA_GAP_SECONDS` before each chunk, so the
    # window the model takes to produce the answer is sleep-dominated and does
    # not shrink on a slow machine. Tokens released as they arrive span it;
    # tokens buffered and flushed at the end collapse to nearly zero, which is
    # the regression this test exists to catch.
    chunks = -(-len(_ANSWER) // _DELTA_CHARS)
    produced_over = (chunks - 1) * _DELTA_GAP_SECONDS
    assert spread > produced_over / 2, (
        f"tokens spanned only {spread:.3f}s of a model that took at least "
        f"{produced_over:.3f}s to produce them -- they were released in a "
        f"burst rather than streamed (run total {total:.3f}s)"
    )
    # And the first one is early, which is what stops the UI looking hung.
    #
    # "Early" measured against the durable write rather than against the run,
    # for the same reason: `token_times[0] < total / 2` counted harness
    # startup, which happens before any token can exist, and so failed
    # whenever setup cost more than the stream -- 0.347s of startup against a
    # 0.615s run, with the tokens themselves perfectly spaced. What matters is
    # that the first token beats the finished answer by a real margin; a
    # harness that buffered would emit them all alongside it.
    message_at = next(at for at, kind in timeline if kind is AgentEventType.MESSAGE)
    assert message_at - token_times[0] > produced_over / 2, (
        f"the first token landed {message_at - token_times[0]:.3f}s before the "
        f"finished answer, out of a {produced_over:.3f}s stream -- the UI would "
        "have sat empty until the run was nearly done"
    )


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


@pytest.mark.asyncio
async def test_a_reply_written_at_reading_pace_reaches_the_reader_as_it_grows(
    monkeypatch,
) -> None:
    """A slow model's reply is shown every flush interval, not in fifty-character jumps.

    Four characters every 25 ms take a third of a second to fill one
    fifty-character chunk, so batching by size alone showed this reply in three
    frames: its first word, one jump, and the rest when the part ended. Text
    that changes about every flush interval is what reads as being written.
    """
    reply = "Sure. I checked the table and found three overdue invoices; here they are."
    chars, gap = 4, 0.025
    timeline = await _timed_events(
        FunctionModel(stream_function=_slow_stream(reply, chars=chars, gap=gap)),
        monkeypatch=monkeypatch,
    )

    frames = [at for at, kind in timeline if kind is AgentEventType.TOKEN]
    produced_over = (-(-len(reply) // chars) - 1) * gap
    # A frame per interval, with room for each to wait one more delta: a delta
    # landing just short of the interval holds its text for the next one.
    assert DEFAULT_TOKEN_FLUSH_SECONDS <= _FLUSH_CONTRACT_SECONDS
    expected = produced_over / (2 * _FLUSH_CONTRACT_SECONDS)
    assert len(frames) >= expected, (
        f"{len(frames)} token frames over a {produced_over:.3f}s reply -- the "
        f"reader saw it jump rather than grow (expected at least {expected:.1f})"
    )

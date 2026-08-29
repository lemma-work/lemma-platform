"""Compaction must never cost the conversation its own request.

These are the regression tests for a run that lost what it was asked for. The
library this processor replaces summarized only the last 16,000 characters of
what it was discarding, folded the user's turns into that prose, and put the
result at the front of the list where the ceiling guard promptly deleted it. The
agent carried on with a summary of a screenshot, invented a task that fit the
remaining context, and told the user that task was the one they had requested.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from app.modules.agent.infrastructure.harnesses.history import (
    enforce_token_ceiling,
    is_pinned_message,
)
from app.modules.agent.infrastructure.harnesses.history_compaction import (
    FAILED_MARKER,
    SUMMARY_MARKER,
    HistoryCompactor,
)

pytestmark = pytest.mark.unit

THE_REQUEST = "Create a 3blue1brown style video explaining the Qwen architecture"


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _work(call_id: str, payload: str) -> list[object]:
    return [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="exec_command",
                    args={"cmd": f"step {call_id}"},
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="exec_command", content=payload, tool_call_id=call_id
                )
            ]
        ),
    ]


def _history(turns: int = 30) -> list[object]:
    messages: list[object] = [_user(THE_REQUEST)]
    for index in range(turns):
        marker = "FIRST_STEP" if index == 0 else f"step-{index}"
        messages.extend(_work(f"c{index}", f"{marker} " + ("output " * 200)))
    return messages


def _model(sink: list[str], reply: str = "the agent was rendering scenes"):
    """A summarizer that records the transcript it was actually given."""

    def respond(messages, info):
        sink.append(str(messages[-1].parts[-1].content))
        return ModelResponse(parts=[TextPart(reply)])

    return FunctionModel(respond)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(usage=RunUsage())


def _compactor(sink: list[str], **kwargs) -> HistoryCompactor:
    options = {"trigger_tokens": 2_000, "keep_messages": 6}
    options.update(kwargs)
    return HistoryCompactor(model=_model(sink), **options)


def _texts(messages: list[object]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


class TestTheRequestSurvives:
    async def test_the_original_request_is_still_there_afterwards(self) -> None:
        """The whole point. Everything else in this file supports it."""
        history = _history()

        compacted = await _compactor([])(_ctx(), history)

        assert any(THE_REQUEST in text for text in _texts(compacted))

    async def test_the_request_is_kept_verbatim_not_paraphrased(self) -> None:
        history = _history()

        compacted = await _compactor([])(_ctx(), history)

        assert compacted[0] is history[0]

    async def test_a_mid_conversation_correction_is_kept_too(self) -> None:
        """The message most likely to be lost, and the most expensive to lose:
        the user changing their mind halfway through."""
        history = _history()
        history.insert(21, _user("actually make it about attention, not training"))

        compacted = await _compactor([])(_ctx(), history)

        assert any("not training" in text for text in _texts(compacted))

    async def test_compaction_actually_makes_the_history_smaller(self) -> None:
        history = _history()

        compacted = await _compactor([])(_ctx(), history)

        assert len(compacted) < len(history)


class TestWhatGetsSummarized:
    async def test_the_whole_span_is_summarized_not_just_its_tail(self) -> None:
        """The library read only the last 16,000 characters of what it was
        discarding, so its summary described the most recent tool call and never
        the work. The beginning is the half that matters."""
        sink: list[str] = []

        await _compactor(sink)(_ctx(), _history())

        assert sink, "the summarizer was never called"
        assert "FIRST_STEP" in sink[0]

    async def test_the_summary_is_marked_so_a_later_pass_knows_its_own_work(
        self,
    ) -> None:
        compacted = await _compactor([])(_ctx(), _history())

        assert any(SUMMARY_MARKER in text for text in _texts(compacted))

    async def test_user_turns_are_not_spent_on_the_summary(self) -> None:
        """They survive verbatim, so paying a summarizer to describe them would
        buy a worse copy of something already kept."""
        sink: list[str] = []

        await _compactor(sink)(_ctx(), _history())

        assert THE_REQUEST not in sink[0]

    async def test_a_history_under_the_trigger_is_untouched(self) -> None:
        history = _history(turns=1)

        compacted = await _compactor([], trigger_tokens=1_000_000)(_ctx(), history)

        assert compacted == history


class TestIdempotence:
    async def test_compacting_twice_changes_nothing_the_second_time(self) -> None:
        """The processor runs on every model request, over history it has
        already processed (`_agent_graph` writes the result back). A pass that
        is not idempotent degrades the context a little on every step."""
        compactor = _compactor([])
        once = await compactor(_ctx(), _history())

        twice = await compactor(_ctx(), list(once))

        assert _texts(twice) == _texts(once)

    async def test_an_existing_summary_is_never_re_summarized(self) -> None:
        """Summarizing a summary is how a compacted history turns to mush."""
        sink: list[str] = []
        compactor = _compactor(sink, trigger_tokens=200, keep_messages=4)
        once = await compactor(_ctx(), _history())

        await compactor(_ctx(), list(once))

        assert all(SUMMARY_MARKER not in transcript for transcript in sink)


class TestTheCeilingGuardRespectsIt:
    async def test_the_summary_outlives_the_ceiling_guard(self) -> None:
        """The guard used to halve the head, and the summary sits at the head --
        so a run paid for a summarization call and discarded it in the same pass.
        """
        compacted = await _compactor([])(_ctx(), _history())

        trimmed = enforce_token_ceiling(compacted, ceiling=500)

        assert any(SUMMARY_MARKER in text for text in _texts(trimmed))

    async def test_the_request_outlives_the_ceiling_guard(self) -> None:
        compacted = await _compactor([])(_ctx(), _history())

        trimmed = enforce_token_ceiling(compacted, ceiling=500)

        assert any(THE_REQUEST in text for text in _texts(trimmed))

    def test_a_tool_result_is_not_mistaken_for_something_a_person_said(self) -> None:
        """A tool's own content rides in a `UserPromptPart` beside its return.
        Pinning that would keep every screenshot forever."""
        tool_message = ModelRequest(
            parts=[
                ToolReturnPart(tool_name="view_image", content="ok", tool_call_id="c1"),
                UserPromptPart(content="the image bytes would ride here"),
            ]
        )

        assert not is_pinned_message(tool_message)
        assert is_pinned_message(_user("but this is mine"))


class TestFailureIsVisible:
    async def test_a_failed_summary_says_so_instead_of_passing_through(self) -> None:
        """The library caught its own failures and returned the original,
        oversized history -- safe for the data, and fatal for the request that
        followed, which the ceiling guard could only fix by amputation."""

        def explode(messages, info):
            raise RuntimeError("summarizer unavailable")

        compactor = HistoryCompactor(
            model=FunctionModel(explode), trigger_tokens=2_000, keep_messages=6
        )
        history = _history()

        compacted = await compactor(_ctx(), history)

        assert len(compacted) < len(history)
        assert any(FAILED_MARKER in text for text in _texts(compacted))
        assert any(THE_REQUEST in text for text in _texts(compacted))


class TestBilling:
    async def test_the_summarization_call_is_charged_to_the_run(self) -> None:
        """It was an LLM call Lemma paid for and never saw: the library built
        its own bare Agent, whose usage never reached the run."""
        ctx = _ctx()

        await _compactor([])(ctx, _history())

        assert ctx.usage.requests >= 1

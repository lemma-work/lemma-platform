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


class TestStaleImagesAreDetached:
    """An image the model has already read must stop costing on every step.

    pydantic-ai keeps a tool's image content in the run's in-memory history, so
    every image is re-uploaded on every model request for the rest of the run. A
    ten-page document viewed at step 2 of a fifteen-step run is sent fourteen
    more times, and the model reads it once.
    """

    def _history_with_image_at(self, position: int, length: int = 20) -> list[object]:
        from pydantic_ai.messages import BinaryContent

        messages: list[object] = [_user(THE_REQUEST)]
        for index in range(length):
            if index == position:
                messages.append(
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name="view_image",
                                content=[
                                    "Successfully read image",
                                    BinaryContent(
                                        data=b"\x89PNG" + b"\x01" * 40_000,
                                        media_type="image/png",
                                    ),
                                ],
                                tool_call_id=f"img{index}",
                            )
                        ]
                    )
                )
            else:
                messages.extend(_work(f"c{index}", "output " * 20))
        return messages

    def _has_binary(self, messages: list[object]) -> bool:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            _is_binary,
        )

        def _items(part):
            content = getattr(part, "content", None)
            return content if isinstance(content, list) else [content]

        return any(
            _is_binary(item)
            for message in messages
            for part in message.parts
            for item in _items(part)
        )

    def test_a_recent_image_keeps_its_pixels(self) -> None:
        """The model may still be looking at it."""
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            strip_stale_images,
        )

        history = self._history_with_image_at(position=19, length=20)

        assert self._has_binary(strip_stale_images(history))

    def test_an_old_image_is_detached(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            strip_stale_images,
        )

        history = self._history_with_image_at(position=1, length=20)

        assert not self._has_binary(strip_stale_images(history))

    def test_the_detached_image_leaves_a_marker(self) -> None:
        """Silence would read as though the tool returned nothing."""
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            STALE_IMAGE_MARKER,
            strip_stale_images,
        )

        stripped = strip_stale_images(self._history_with_image_at(position=1))

        assert any(
            STALE_IMAGE_MARKER in str(getattr(part, "content", ""))
            for message in stripped
            for part in message.parts
        )

    def test_it_is_idempotent(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            strip_stale_images,
        )

        history = self._history_with_image_at(position=1)
        once = strip_stale_images(history)

        assert strip_stale_images(once) == once

    def test_a_short_history_is_untouched(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            strip_stale_images,
        )

        history = [_user(THE_REQUEST)]

        assert strip_stale_images(history) is history

    def test_tool_pairing_survives(self) -> None:
        """Rewriting content must not disturb which call a result belongs to."""
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            strip_stale_images,
        )

        history = self._history_with_image_at(position=1)

        stripped = strip_stale_images(history)

        before = [
            part.tool_call_id
            for message in history
            for part in message.parts
            if hasattr(part, "tool_call_id")
        ]
        after = [
            part.tool_call_id
            for message in stripped
            for part in message.parts
            if hasattr(part, "tool_call_id")
        ]
        assert before == after


class TestThePinnedSetIsBounded:
    """Keeping every user turn is right until there are a thousand of them.

    At that point the pinned set alone fills the budget and the agent has no
    room left to do the work. When it has to give, the first message stays: it
    is the request, and losing it is the failure this module exists to prevent.
    """

    def _many_questions(self, count: int) -> list[object]:
        messages: list[object] = [_user(THE_REQUEST)]
        for index in range(count):
            messages.append(_user(f"follow-up number {index}"))
            messages.extend(_work(f"c{index}", "output " * 50))
        return messages

    async def test_the_original_request_survives_the_bound(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
        )

        history = self._many_questions(MAX_PINNED_USER_MESSAGES * 3)

        compacted = await _compactor([])(_ctx(), history)

        assert any(THE_REQUEST in text for text in _texts(compacted))

    async def test_the_most_recent_questions_survive_too(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
        )

        count = MAX_PINNED_USER_MESSAGES * 3
        history = self._many_questions(count)

        compacted = await _compactor([])(_ctx(), history)

        assert any(f"follow-up number {count - 1}" in t for t in _texts(compacted))

    async def test_the_pinned_set_stops_growing(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
        )

        history = self._many_questions(MAX_PINNED_USER_MESSAGES * 3)

        compacted = await _compactor([])(_ctx(), history)

        # The bound is on what the compacted head carries. The recent tail is
        # kept whole by design, so its own user turns are on top of it.
        pinned = [m for m in compacted if is_pinned_message(m)]
        assert len(pinned) <= MAX_PINNED_USER_MESSAGES + 1 + 6  # summary + tail

    async def test_folding_a_users_words_is_declared(self) -> None:
        """Silently turning what somebody said into prose about what they said
        is the drop this whole module removes."""
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
        )

        history = self._many_questions(MAX_PINNED_USER_MESSAGES * 3)

        compacted = await _compactor([])(_ctx(), history)

        assert any("earlier message(s) from the user" in t for t in _texts(compacted))

    async def test_nothing_is_declared_when_nothing_was_folded(self) -> None:
        compacted = await _compactor([])(_ctx(), _history())

        assert not any(
            "earlier message(s) from the user" in t for t in _texts(compacted)
        )

    async def test_folded_turns_are_summarized_not_simply_lost(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
        )

        sink: list[str] = []
        history = self._many_questions(MAX_PINNED_USER_MESSAGES * 3)

        await _compactor(sink)(_ctx(), history)

        assert "follow-up number 0" in sink[0]


class TestSyntheticMessagesNeverDisplaceTheRequest:
    """The processors write into history the next pass reads back.

    `_ensure_leading_user_message` inserts a placeholder when a trimmed history
    would otherwise open with an assistant turn, and the graph writes that back
    (`ctx.state.message_history[:] = messages`). Unmarked, the placeholder looks
    exactly like a user turn — so it gets pinned, and on the next request it is
    the *first* pinned message. `_bounded_pins` keeps the first and folds the
    rest, so a long conversation would keep a placeholder saying nothing and
    fold the user's actual request. This is that failure, caught once.
    """

    async def _leading_placeholder(self):
        from pydantic_ai.messages import ModelResponse, TextPart

        from app.modules.agent.domain.value_objects import HarnessOptions
        from app.modules.agent.infrastructure.harnesses.history import (
            build_history_processors,
        )

        guard = build_history_processors(
            HarnessOptions(model_name="m"), summarization_model="openai:gpt-4.1"
        )[-1]
        opened = await guard([ModelResponse(parts=[TextPart("assistant first")])])
        return opened[0]

    async def test_the_placeholder_is_not_mistaken_for_a_user_turn(self) -> None:
        assert not is_pinned_message(await self._leading_placeholder())

    async def test_it_does_not_take_the_first_pinned_slot(self) -> None:
        from app.modules.agent.infrastructure.harnesses.history_compaction import (
            MAX_PINNED_USER_MESSAGES,
            _bounded_pins,
        )

        placeholder = await self._leading_placeholder()
        request = _user(THE_REQUEST)
        head = [placeholder, request] + [
            _user(f"follow up {index}") for index in range(MAX_PINNED_USER_MESSAGES * 2)
        ]

        pins = _bounded_pins([m for m in head if is_pinned_message(m)])

        assert pins[0] is request

    async def test_it_is_added_only_once_however_many_passes_run(self) -> None:
        """It runs on every model request over history it already rewrote."""
        from pydantic_ai.messages import ModelResponse, TextPart

        from app.modules.agent.domain.value_objects import HarnessOptions
        from app.modules.agent.infrastructure.harnesses.history import (
            build_history_processors,
        )

        guard = build_history_processors(
            HarnessOptions(model_name="m"), summarization_model="openai:gpt-4.1"
        )[-1]
        history = [ModelResponse(parts=[TextPart("assistant first")])]

        once = await guard(history)
        twice = await guard(once)

        assert len(twice) == len(once)

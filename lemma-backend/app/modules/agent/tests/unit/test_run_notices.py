"""Telling a run what is about to happen to it, in a turn a provider accepts.

Two ceilings reach a run this way: the budget it stops at, and the history size
past which its older turns become a summary. Both used to arrive without notice
-- one cutting the run off mid-thought, the other quietly taking detail away --
and both are only useful early, while there is still room to act.

The delivery is the fiddly part and is what these pin. A notice is not a turn of
its own: providers that require alternating roles reject two user turns in a
row, and the run's tool returns are already sitting in the last request, which
is where it is looking.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from app.modules.agent.infrastructure.harnesses.history import append_notices
from app.modules.agent.infrastructure.harnesses.history_compaction import (
    HistoryCompactor,
)

pytestmark = pytest.mark.unit


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _user_texts(message: object) -> list[str]:
    return [
        str(part.content)
        for part in getattr(message, "parts", ())
        if isinstance(part, UserPromptPart)
    ]


def test_a_notice_joins_the_last_request_instead_of_opening_a_new_turn():
    """Anthropic rejects two user turns in a row, so a notice appended as its
    own request would fail the very request it was trying to inform."""
    history = [_assistant("thinking"), _user("here are your tool returns")]

    result = append_notices(list(history), ["you are near the limit"])

    assert len(result) == len(history), "no extra turn"
    assert _user_texts(result[-1]) == [
        "here are your tool returns",
        "you are near the limit",
    ]


def test_a_notice_opens_a_request_when_the_last_turn_is_the_models():
    """Nothing to join, and appending after a model turn keeps roles alternating."""
    result = append_notices([_assistant("thinking")], ["you are near the limit"])

    assert len(result) == 2
    assert isinstance(result[-1], ModelRequest)
    assert _user_texts(result[-1]) == ["you are near the limit"]


def test_a_notice_does_not_stay_in_the_runs_own_history():
    """The list a processor is handed belongs to the run. A part appended in
    place would still be there next turn, so a one-shot notice would become a
    permanent line in every later prompt."""
    original = _user("here are your tool returns")
    history = [original]

    append_notices(list(history), ["you are near the limit"])

    assert _user_texts(original) == ["here are your tool returns"]


def test_nothing_is_touched_when_there_is_nothing_to_say():
    history = [_user("hello")]

    assert append_notices(list(history), []) == history


async def test_the_run_is_told_before_compaction_takes_its_older_turns():
    """Compaction is lossy on purpose and arrives silently: the run finds out
    by no longer remembering. Warned first, it can put what it needs somewhere
    that survives."""
    summarized: list[str] = []

    def _summarize(messages, info):
        summarized.append("called")
        return ModelResponse(parts=[TextPart("a summary")])

    compactor = HistoryCompactor(
        model=FunctionModel(_summarize),
        trigger_tokens=100_000,
        keep_messages=6,
        # Low enough that a two-message history is already "near", without
        # having to build one big enough to approach a real threshold.
        warn_at=0.0000001,
    )
    ctx = SimpleNamespace(usage=RunUsage())
    history = [_user("a question"), _assistant("an answer")]

    warned = await compactor(ctx, list(history))
    again = await compactor(ctx, list(history))

    assert summarized == [], "warning only -- nothing was summarized"
    assert any("older parts" in text for text in _user_texts(warned[-1]))
    assert not any("older parts" in text for text in _user_texts(again[-1])), (
        "said once: every turn from here on is past the threshold"
    )


async def test_the_warning_comes_back_for_every_later_compaction():
    """A long run compacts more than once, and the second pass takes just as
    much as the first. Warned only on the first, a run that worked for an hour
    would be told once and then quietly lose the rest -- which is the behaviour
    this notice exists to replace, arriving later."""
    summarized: list[str] = []

    def _summarize(messages, info):
        summarized.append("called")
        return ModelResponse(parts=[TextPart("a summary")])

    compactor = HistoryCompactor(
        model=FunctionModel(_summarize),
        trigger_tokens=200,
        keep_messages=2,
        warn_at=0.0000001,
    )
    ctx = SimpleNamespace(usage=RunUsage())
    long_history = [_user(f"step {n}") for n in range(60)]
    short_history = [_user("a question"), _assistant("an answer")]

    first = await compactor(ctx, list(short_history))
    await compactor(ctx, long_history)
    after = await compactor(ctx, list(short_history))

    assert summarized == ["called"], "the middle call is the one that compacted"
    assert any("older parts" in text for text in _user_texts(first[-1]))
    assert any("older parts" in text for text in _user_texts(after[-1])), (
        "re-armed: the next cycle gets its own warning"
    )

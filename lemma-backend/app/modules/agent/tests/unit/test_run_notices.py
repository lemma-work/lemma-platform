"""Telling a run what is about to happen to it, without costing it the cache.

Two ceilings reach a run this way: the budget it stops at, and the history size
past which its older turns become a summary. Both used to arrive without notice
-- one cutting the run off mid-thought, the other quietly taking detail away.

Where the notice goes matters as much as what it says, and for a reason that is
easy to undo by accident. The request is cached as a prefix, so editing a
message the provider has already read means every token from that point on is
read again. A notice therefore arrives as its own message at the tail, never as
an edit to an existing one -- and before the user's turn, because a model
answers the last thing it was asked. ``current_time.py`` reaches the same three
conclusions for the same reasons; this follows it deliberately.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RunUsage

from app.modules.agent.capabilities.run_notices import RunNoticeCapability
from app.modules.agent.domain.run_notices import RunNotices
from app.modules.agent.infrastructure.harnesses.history_compaction import (
    HistoryCompactor,
)
from app.modules.agent.infrastructure.pydantic_ai_compat import ModelRequestContext

pytestmark = pytest.mark.unit


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _tool_returns(text: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name="t", content=text, tool_call_id="c1")]
    )


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _texts(message: object) -> list[str]:
    return [
        str(part.content)
        for part in getattr(message, "parts", ())
        if isinstance(part, UserPromptPart)
    ]


async def _deliver(capability, messages):
    request_context = ModelRequestContext(
        model=object(),
        messages=list(messages),
        model_settings=None,
        model_request_parameters=None,
    )
    result = await capability.before_model_request(
        SimpleNamespace(usage=RunUsage()), request_context
    )
    return result.messages


async def test_a_notice_arrives_before_the_users_turn():
    """A model answers the last user message. A notice after it competes with
    the actual instruction; consecutive requests merge in order, so sitting in
    front of it costs nothing and changes nothing about what is being asked."""
    notices = RunNotices()
    notices.post("you are near the limit")
    history = [_assistant("thinking"), _user("please continue")]

    delivered = await _deliver(RunNoticeCapability(notices), history)

    assert _texts(delivered[-1]) == ["please continue"], "the ask stays last"
    assert _texts(delivered[-2]) == ["you are near the limit"]


async def test_a_notice_is_its_own_message_and_edits_nothing():
    """The cache is a prefix. Appending a part to the last request rewrites a
    message the provider has already read, so everything from there is read
    again; a new message at the tail leaves all of it untouched."""
    notices = RunNotices()
    notices.post("compaction is coming")
    returns = _tool_returns("a big tool result")
    history = [_assistant("calling a tool"), returns]

    delivered = await _deliver(RunNoticeCapability(notices), history)

    assert delivered[:2] == history, "not one earlier message was rebuilt"
    assert delivered[1] is returns, "the same object, not a copy"
    assert _texts(delivered[-1]) == ["compaction is coming"]


async def test_a_notice_is_delivered_once():
    """Left in the mailbox it would ride every later request -- and since the
    delivered message is written back into run state, the prompt would grow a
    copy of it per model step."""
    notices = RunNotices()
    notices.post("said once")
    capability = RunNoticeCapability(notices)
    history = [_user("go on")]

    first = await _deliver(capability, history)
    second = await _deliver(capability, history)

    assert any("said once" in t for m in first for t in _texts(m))
    assert not any("said once" in t for m in second for t in _texts(m))


async def test_nothing_is_added_when_there_is_nothing_to_say():
    history = [_user("hello")]

    assert await _deliver(RunNoticeCapability(RunNotices()), history) == history


def _compactor(notices: RunNotices, **kwargs) -> HistoryCompactor:
    def _summarize(messages, info):
        return ModelResponse(parts=[TextPart("a summary")])

    options = {
        "trigger_tokens": 200,
        "keep_messages": 2,
        "notices": notices,
        # Low enough that a short history already counts as near, without
        # building one big enough to approach a realistic threshold.
        "warn_at": 0.0000001,
    }
    options.update(kwargs)
    return HistoryCompactor(model=FunctionModel(_summarize), **options)


async def test_the_run_is_told_before_compaction_takes_its_older_turns():
    """Compaction is lossy on purpose and arrives silently: the run finds out
    by no longer remembering. Warned first, it can put what it needs somewhere
    that outlives the transcript."""
    notices = RunNotices()
    compactor = _compactor(notices)
    ctx = SimpleNamespace(usage=RunUsage())

    await compactor(ctx, [_user("a question"), _assistant("an answer")])

    posted = notices.take()
    assert len(posted) == 1
    assert "older parts" in posted[0]
    assert "todo list" in posted[0], "names what actually survives"


async def test_the_warning_comes_back_for_every_later_compaction():
    """A long run compacts more than once, and the second pass takes just as
    much as the first. Warned only on the first, a run that worked for an hour
    would be told once and then quietly lose the rest."""
    notices = RunNotices()
    compactor = _compactor(notices)
    ctx = SimpleNamespace(usage=RunUsage())
    short = [_user("a question"), _assistant("an answer")]

    await compactor(ctx, list(short))
    assert len(notices.take()) == 1

    await compactor(ctx, [_user(f"step {n}") for n in range(60)])
    assert notices.take() == [], "a pass that compacts does not also warn"

    await compactor(ctx, list(short))
    assert len(notices.take()) == 1, "re-armed for the next cycle"

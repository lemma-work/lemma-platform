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

import inspect
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from app.modules.agent.infrastructure.harness.lemma.history_parts import (
    RunContextCapability,
)
from app.modules.agent.infrastructure.harness.lemma.history_parts import (
    RunNoticeCapability,
)
from app.modules.agent.domain.run_notices import RunNotices
from app.modules.agent.infrastructure.harness.lemma.history_compaction import (
    HistoryCompactor,
)
from app.modules.agent.infrastructure.providers.pydantic_ai_compat import (
    ModelRequestContext,
)

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
        SimpleNamespace(usage=RunUsage(), messages=list(messages)), request_context
    )
    return result.messages


async def test_a_notice_arrives_before_the_users_turn():
    """A model answers the last user message. A notice after it competes with
    the actual instruction, so it goes in front of it, in the same request."""
    notices = RunNotices()
    notices.post("you are near the limit")
    history = [_assistant("thinking"), _user("please continue")]

    delivered = await _deliver(RunNoticeCapability(notices), history)

    assert _texts(delivered[-1]) == ["you are near the limit", "please continue"]


async def test_a_notice_rides_in_the_request_about_to_be_sent():
    """Never a request of its own: pydantic-ai merges consecutive requests, so
    one would be logged as sent and then replaced by the merged one. The
    trailing request is the one nothing has sent yet; the notice goes after
    what it carries, and nothing before it is rebuilt."""
    notices = RunNotices()
    notices.post("compaction is coming")
    calling = _assistant("calling a tool")
    returns = _tool_returns("a big tool result")
    history = [calling, returns]

    delivered = await _deliver(RunNoticeCapability(notices), history)

    assert len(delivered) == 2 and delivered[0] is calling
    assert delivered[1] is returns, "the same object, as the run log knows it"
    assert _texts(delivered[-1])[-1] == "compaction is coming"


async def test_a_notice_is_delivered_once():
    """Left in the mailbox it would ride every later request -- and since the
    delivered message is written back into run state, the prompt would grow a
    copy of it per model step."""
    notices = RunNotices()
    notices.post("said once")
    capability = RunNoticeCapability(notices)

    first = await _deliver(capability, [_user("go on")])
    second = await _deliver(capability, [_user("go on")])

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


async def test_a_notice_is_measured_by_the_ceiling_it_has_to_fit_inside():
    """Capabilities run in registration order, and the history processors
    include the hard-ceiling guard.

    Registered after them, this one appends to a request the guard has already
    measured and passed: the notice rides along unaccounted for, and the
    ceiling stops being a ceiling. It is not a large overflow -- a notice is a
    paragraph -- but it is an overflow of the one limit whose whole job is to
    keep a request inside what the model will accept, and the guard exists
    because being wrong about that costs the whole turn.

    Pinned on the assembled order rather than by measuring tokens, because the
    ordering *is* the guarantee.
    """
    from app.modules.agent.infrastructure.harness.lemma import harness

    source = inspect.getsource(harness.PydanticAIHarness)
    notice_at = source.index("capabilities.append(RunNoticeCapability")
    processors_at = source.index("history_capabilities(options")

    assert notice_at < processors_at, (
        "RunNoticeCapability must be registered before the history processors, "
        "or the hard-ceiling guard never sees the notice it has to fit"
    )


def _two_step_agent(
    capabilities: list[object], seen: list[list[ModelMessage]]
) -> Agent[object, str]:
    """A run that calls a tool and then answers: two model requests."""

    def model_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(list(messages))
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("step", {})])
        return ModelResponse(parts=[TextPart("done")])

    agent: Agent[object, str] = Agent(
        FunctionModel(model_fn), capabilities=capabilities
    )

    @agent.tool_plain
    def step() -> str:
        return "stepped"

    return agent


def _untouched(messages: list[ModelMessage]) -> list[ModelMessage]:
    return messages


async def test_a_notice_stays_in_the_history_whatever_is_registered_after_it():
    """Delivered once, it has to be on every later request of the run too.

    Since pydantic-ai 2.55 only ``ProcessHistory`` writes a hook's result back
    into the run's history. The notice used to stay only because the history
    processors registered after it wrote the whole list back; registered last,
    it reached the first request and vanished from the second, which then
    diverged from the prefix the provider had cached.
    """
    notices = RunNotices()
    notices.post("the budget is nearly spent")
    seen: list[list[ModelMessage]] = []
    agent = _two_step_agent(
        [ProcessHistory(_untouched), RunNoticeCapability(notices)], seen
    )

    await agent.run("hello", deps=object())

    assert len(seen) == 2
    for request in seen:
        texts = [t for m in request for t in _texts(m)]
        assert texts.count("the budget is nearly spent") == 1, texts


async def test_the_run_context_part_is_where_the_history_has_it():
    """Placed once before the turn, and kept there in the run's history,
    whatever is registered after it."""
    context = "<run_context>\nnow\n</run_context>"
    seen: list[list[ModelMessage]] = []
    agent = _two_step_agent(
        [ProcessHistory(_untouched), RunContextCapability(context)], seen
    )

    result = await agent.run("hello", deps=object())

    kept = [t for m in result.all_messages() for t in _texts(m)]
    assert kept.count(context) == 1
    assert kept.index(context) == kept.index("hello") - 1
    second = [t for m in seen[1] for t in _texts(m)]
    assert second.index(context) == second.index("hello") - 1

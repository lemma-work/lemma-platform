"""What bounds a run: the stop signal's timing, and the spend budget's arithmetic.

The harness polls ``should_stop`` at every streaming checkpoint — per token
delta, per part, per tool call. Asking the database each time issued one
``SELECT`` per token across every concurrent run, which was the dominant
per-token database load under streaming. So the throttling and the stickiness
are the behaviour, and both are timing rather than persistence: injected here
rather than patched, so what is under test is the logic and not a stand-in for
half of it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.run_budget import (
    BudgetDimension,
    RunBudget,
    RunSpend,
)
from app.modules.agent.services.run_limits import budget_for_run, throttled_sticky

pytestmark = pytest.mark.unit


def _counting(answers):
    """A check that records how often it was consulted."""
    calls = {"n": 0}
    queue = list(answers)

    async def _check() -> bool:
        calls["n"] += 1
        return queue.pop(0) if queue else False

    return _check, calls


async def test_the_stop_check_is_asked_at_most_once_per_interval():
    check, calls = _counting([False] * 10)
    clock = {"t": 1000.0}
    interval = 1.0
    should_stop = throttled_sticky(check, interval=interval, clock=lambda: clock["t"])

    assert await should_stop() is False
    assert calls["n"] == 1

    for _ in range(100):
        assert await should_stop() is False
    assert calls["n"] == 1, "a hundred checkpoints must not be a hundred queries"

    clock["t"] += interval + 0.01
    assert await should_stop() is False
    assert calls["n"] == 2


async def test_once_stopped_it_stops_asking():
    check, calls = _counting([True])
    clock = {"t": 0.0}
    should_stop = throttled_sticky(check, interval=1.0, clock=lambda: clock["t"])

    assert await should_stop() is True
    for _ in range(10):
        clock["t"] += 100.0
        assert await should_stop() is True
    assert calls["n"] == 1, "a stop is final; re-asking only costs queries"


def _spend(**limits) -> RunSpend:
    return RunSpend(
        budget=RunBudget(
            model_requests=limits.get("model_requests", 3),
            wall_clock_seconds=limits.get("wall_clock_seconds", 100.0),
            consecutive_tool_failures=limits.get("consecutive_tool_failures", 2),
        )
    )


def test_a_run_within_its_budget_is_not_stopped():
    spend = _spend()
    spend.record_model_request()

    assert spend.exhausted(elapsed_seconds=1.0) is None


def test_the_step_ceiling_names_itself():
    spend = _spend(model_requests=2)
    spend.record_model_request()
    spend.record_model_request()

    exhausted = spend.exhausted(elapsed_seconds=0.0)

    assert exhausted is not None
    assert exhausted.dimension is BudgetDimension.MODEL_REQUESTS
    assert "steps" in exhausted.reason


def test_the_clock_catches_the_run_that_waits_rather_than_loops():
    """Every call is cheap and the hours are not, so a step count never trips."""
    spend = _spend(wall_clock_seconds=60.0)

    exhausted = spend.exhausted(elapsed_seconds=120.0)

    assert exhausted is not None
    assert exhausted.dimension is BudgetDimension.WALL_CLOCK


def test_a_success_clears_the_failing_streak():
    """Consecutive, not total: a long run doing real work fails a tool now and
    then, and stopping it for that punishes the runs that are converging."""
    spend = _spend(consecutive_tool_failures=2)
    spend.record_tool_outcome(failed=True)
    spend.record_tool_outcome(failed=False)
    spend.record_tool_outcome(failed=True)

    assert spend.exhausted(elapsed_seconds=0.0) is None

    spend.record_tool_outcome(failed=True)
    exhausted = spend.exhausted(elapsed_seconds=0.0)
    assert exhausted is not None
    assert exhausted.dimension is BudgetDimension.TOOL_FAILURES


def test_carrying_on_is_a_new_run_with_a_new_budget():
    """The resumed run builds its own `RunSpend`, so the allowance is fresh.

    Pinned because the alternative — extending the spent one in place — is the
    obvious-looking design, and it cannot work: the wall clock is measured from
    when the run started and there is nothing to reset it to.
    """
    first = budget_for_run(type("R", (), {"metadata": {"source": "user_message"}})())
    first.record_model_request()

    second = budget_for_run(type("R", (), {"metadata": {"source": "user_message"}})())

    assert second.model_requests == 0
    assert not hasattr(second, "extensions")


def test_a_dimension_set_to_zero_is_switched_off():
    spend = _spend(model_requests=0, wall_clock_seconds=0, consecutive_tool_failures=0)
    for _ in range(50):
        spend.record_model_request()
        spend.record_tool_outcome(failed=True)

    assert spend.exhausted(elapsed_seconds=10_000.0) is None


def test_an_unattended_run_reads_its_own_clock():
    """Nobody is waiting on an unattended run, so a deployment may want to let
    it go longer — but nobody is watching the spend either, so it stays bounded,
    and by default it gets the same two hours rather than an assumed extra.

    What is pinned is that the two read *different settings*. Equal defaults
    would otherwise hide a wiring mistake: pointing both at one of them looks
    identical until somebody raises the unattended one and nothing changes.
    """

    class _Run:
        metadata = {"source": "agent_wait"}

    class _InteractiveRun:
        metadata = {"source": "user_message"}

    unattended = budget_for_run(_Run())
    interactive = budget_for_run(_InteractiveRun())

    assert unattended.budget.wall_clock_seconds == (
        agent_settings.agent_run_budget_unattended_wall_clock_seconds
    )
    assert interactive.budget.wall_clock_seconds == (
        agent_settings.agent_run_budget_wall_clock_seconds
    )
    assert unattended.budget.wall_clock_seconds >= (
        interactive.budget.wall_clock_seconds
    )


async def test_a_run_past_its_budget_pauses_and_asks_rather_than_stopping():
    """The enforcement point, and the shape everything downstream depends on.

    A budget trip leaves by the path a pause already takes: it raises
    `AgentInputRequired`, which `pydantic_ai` turns into WAITING, and it queues a
    `request_approval` card first so there is something for the person to answer.
    Hard-stopping instead would kill a nearly-finished run and make the person
    re-ask, and work redone because the first attempt was discarded is already
    one of the larger costs here.
    """
    import asyncio

    from app.modules.agent.domain.harness_options import HarnessOptions
    from app.modules.agent.domain.run_budget_pause import is_budget_pause
    from app.modules.agent.domain.value_objects import AgentEventType
    from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import (
        NodeLoop,
    )
    from app.modules.agent.tools.tool_errors import AgentInputRequired

    queue: asyncio.Queue = asyncio.Queue()
    loop = NodeLoop(
        run_context=lambda _history: None,
        queue=queue,
        streamer=None,
        options=HarnessOptions(
            model_name="test-model",
            spend=RunSpend(
                budget=RunBudget(
                    model_requests=2,
                    wall_clock_seconds=0,
                    consecutive_tool_failures=0,
                )
            ),
        ),
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        final_output_message=lambda **_kwargs: None,
    )

    await loop._spend_a_model_request()
    assert queue.empty(), "a run inside its budget must not be interrupted"

    with pytest.raises(AgentInputRequired) as raised:
        await loop._spend_a_model_request()

    assert raised.value.kind == "request_approval"
    (_label, event) = queue.get_nowait()
    assert event.type is AgentEventType.MESSAGE
    assert is_budget_pause(event.data.tool_args)
    # The card must not name a real tool, or the approval executor tries to run
    # it when the person says yes.
    assert event.data.tool_args.get("args") is None


async def test_a_run_with_no_budget_is_never_interrupted():
    """`spend=None` is what every caller did before budgets existed."""
    import asyncio

    from app.modules.agent.domain.harness_options import HarnessOptions
    from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import (
        NodeLoop,
    )

    queue: asyncio.Queue = asyncio.Queue()
    loop = NodeLoop(
        run_context=lambda _history: None,
        queue=queue,
        streamer=None,
        options=HarnessOptions(model_name="test-model"),
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        final_output_message=lambda **_kwargs: None,
    )

    for _ in range(100):
        await loop._spend_a_model_request()

    assert queue.empty()


def test_a_run_is_warned_before_a_ceiling_rather_than_at_it():
    """A backstop reached without warning is a trap: the run is cut off holding
    work it could have landed. The notice arrives with room still left."""
    spend = _spend(model_requests=100)
    spend.model_requests = 79

    assert spend.approaching(elapsed_seconds=0.0, at=0.8) is None

    spend.model_requests = 80
    warning = spend.approaching(elapsed_seconds=0.0, at=0.8)

    assert warning is not None
    assert warning.dimension is BudgetDimension.MODEL_REQUESTS
    assert spend.exhausted(elapsed_seconds=0.0) is None, "warned, not stopped"
    assert "80" in warning.notice and "100" in warning.notice, (
        "the run is told how much room is left, not just that it is running out"
    )


def test_each_ceiling_is_warned_about_once():
    """Every step from the threshold on is past it, so a warning that repeated
    would fill the context it is trying to help the run spend well."""
    spend = _spend(model_requests=10)
    spend.model_requests = 9

    first = spend.approaching(elapsed_seconds=0.0, at=0.8)
    second = spend.approaching(elapsed_seconds=0.0, at=0.8)

    assert first is not None
    assert second is None, "said once per dimension"


def test_a_disabled_dimension_is_never_warned_about():
    """Zero switches a ceiling off, and a ceiling that cannot be reached has
    nothing to warn about -- `spent >= 0 * fraction` would otherwise fire on
    the first step of every run."""
    spend = _spend(
        model_requests=0, wall_clock_seconds=0.0, consecutive_tool_failures=0
    )
    spend.model_requests = 5000

    assert spend.approaching(elapsed_seconds=99_999.0, at=0.8) is None


def test_warning_switches_off_outside_a_fraction():
    """The setting is a fraction of a ceiling; 0 or 1 means "do not warn",
    not "warn always" or "warn at the ceiling I already enforce"."""
    spend = _spend(model_requests=10)
    spend.model_requests = 10

    assert spend.approaching(elapsed_seconds=0.0, at=0.0) is None
    assert spend.approaching(elapsed_seconds=0.0, at=1.0) is None


def test_both_approvals_renew_and_neither_retires_the_guard():
    """ "Approve for session" means "this call again, without me" everywhere
    else, and is recorded per permission against the tool being approved. There
    is no tool on a budget card, and what it would switch off is the only thing
    standing between a run that has stopped converging and the rest of the
    conversation. So it renews like a single approval rather than retiring the
    guard, and this pins that: the docstring says so, and a docstring is not
    enforcement."""
    from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
    from app.modules.agent.services.conversation_resume_return import (
        _budget_decision_return,
    )

    once = _budget_decision_return(AgentRunApprovalDecision.APPROVE_ONCE)
    session = _budget_decision_return(AgentRunApprovalDecision.APPROVE_FOR_SESSION)
    denied = _budget_decision_return(AgentRunApprovalDecision.DENY)

    assert once == session, "a session approval is not a licence to stop asking"
    assert "renewed" in str(once["message"])
    assert denied != once
    # Denial is a decision, not a failure: an agent told its work failed tries
    # to repair something that was never broken.
    assert denied["success"] is True
    assert "Stop here" in str(denied["message"])


async def test_a_paused_run_does_not_export_as_a_failed_run():
    """The run span is the other half of "a pause is not a failure".

    Marking the tool span was not enough. Every pause also unwinds through the
    span the harness opened for the whole run, and a span context manager
    stamps ERROR on anything that leaves that way -- so in the backend where
    error rates are read, every `ask_user` and every `wait_for` still closed
    its run as failed. Verified against live traces: the tool spans came back
    clean and the `agent run` spans did not.

    Inside `drive_once` the current span *is* that run span, which is the only
    place left that can mark it before it closes. `StatusCode.OK` is final in
    the SDK, so it survives the unwind.
    """
    import asyncio
    from contextlib import asynccontextmanager

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
    from opentelemetry.trace import StatusCode

    from app.modules.agent.domain.harness_options import HarnessOptions
    from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import (
        NodeLoop,
    )
    from app.modules.agent.tools.tool_errors import AgentInputRequired

    exported: list = []

    class _Collect(SpanExporter):
        def export(self, spans):
            exported.extend(spans)

        def shutdown(self):
            return None

    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(_Collect()))

    class _PausingRun:
        """A graph that pauses on its first node, the way `ask_user` does."""

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AgentInputRequired("call-1", "ask_user")

    @asynccontextmanager
    async def _run_context(_history):
        yield _PausingRun()

    loop = NodeLoop(
        run_context=_run_context,
        queue=asyncio.Queue(),
        streamer=None,
        options=HarnessOptions(model_name="test-model"),
        agent_run_id=uuid4(),
        conversation_id=uuid4(),
        final_output_message=lambda **_kwargs: None,
    )

    # Stands in for the span pydantic-ai opens around the whole run; the probe
    # that settled this showed `agent run` is what `drive_once` runs inside.
    before = len(exported)
    tracer = trace.get_tracer("app.tests.run_span")
    # `pytest.raises` goes OUTSIDE the span, or it swallows the exception
    # before the context manager sees it and nothing ever stamps ERROR -- a
    # test that passes against the unfixed code and proves nothing.
    with pytest.raises(AgentInputRequired):
        with tracer.start_as_current_span("agent run"):
            await loop.drive_once(None, {}, {})

    (span,) = exported[before:]
    assert span.name == "agent run"
    assert span.status.status_code is not StatusCode.ERROR, (
        "a paused run still closes as a failed run"
    )

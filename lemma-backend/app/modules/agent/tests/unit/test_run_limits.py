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


def test_an_unattended_run_gets_the_longer_clock():
    """Nobody is waiting, so a slow run costs nothing anybody feels — and nobody
    is watching the spend either, which is why it is still bounded."""

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
    assert unattended.budget.wall_clock_seconds > interactive.budget.wall_clock_seconds


async def test_a_run_past_its_budget_pauses_and_asks_rather_than_stopping():
    """The enforcement point, and the shape everything downstream depends on.

    A budget trip leaves by the path a pause already takes: it raises
    `AgentInputRequired`, which `pydantic_ai` turns into WAITING, and it queues a
    `request_approval` card first so there is something for the person to answer.
    Hard-stopping instead would kill an 80%-done run and make the person re-ask,
    which is where the re-work cost the study measured already lives.
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

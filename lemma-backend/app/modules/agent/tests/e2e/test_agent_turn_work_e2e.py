"""Agent turns on the work runtime, against real Postgres, in-process.

The API starts a turn the way production does; the runtime's own dispatchers
claim and execute it with the deterministic mock model. Each test is one
property the old transport could not hold:

* the run, its message and its turn commit together, and the finalize writes
  every follow-up with the run's end;
* two dispatchers never run two turns of one conversation;
* a stop beats a queued turn with no harness call, and reaches a running one;
* a finalize that rolls back writes no follow-ups, and the retry writes them once;
* a long silent step keeps its lease (the heartbeat is the runtime's, not the
  agent loop's);
* a parent and its children finishing together deliver every result once,
  without deadlocking.

Worker-process behaviour (SIGKILL, SIGTERM) is in
``test_agent_turn_worker_e2e.py``; replay classes in
``test_turn_replay_classes_e2e.py``.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.infrastructure.work import RetryPolicy, WorkClass
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.agent.events.turn_work import TurnFences, TurnRunner
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.harness_wiring import (
    build_harness_registry,
    worker_uow_factory,
)
from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    runtime_sessions,
    rows,
    scalar,
    turn_pod,
    turn_registry,
    write,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call

pytestmark = [pytest.mark.e2e]


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


@pytest.fixture
def model_latency(monkeypatch):
    """Hold the mock model inside each request for ``ms`` milliseconds."""

    def _set(ms: int) -> None:
        monkeypatch.setattr(settings, "e2e_mock_llm_latency_ms", ms)

    return _set


async def _pace(seconds: float) -> None:
    """The interval of a sampling or claiming loop that runs alongside the
    turns -- not a wait for state, which goes through ``eventually``."""
    with suppress(TimeoutError):
        await asyncio.wait_for(asyncio.Event().wait(), seconds)


async def _items(kind: str) -> int:
    return await scalar("SELECT count(*) FROM work_items WHERE kind = :k", k=kind)


@pytest.mark.drives_work
async def test_a_turn_commits_with_its_run_and_finishes_with_its_followups(
    pod: TurnPod,
) -> None:
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("Done."),
        ]
    )
    started = await pod.append(conversation)
    run_id = started["agent_run_id"]

    [turn] = await rows(
        "SELECT kind, class, dedup_key, concurrency_key, concurrency_limit, state "
        "FROM work_items WHERE dedup_key = :key",
        key=f"agent-run:{run_id}",
    )
    assert turn == (
        "agent.turn",
        "interactive",
        f"agent-run:{run_id}",
        f"conversation:{conversation}",
        1,
        "queued",
    )

    ran = await drain(turn_registry())

    assert ran >= 2, "the turn and its follow-up"
    assert await pod.run_status(run_id) == "COMPLETED"
    kinds = [m["kind"] for m in await pod.messages(conversation)]
    assert kinds.count("TOOL_CALL") == 1 and kinds.count("TOOL_RETURN") == 1
    left = await rows(
        "SELECT kind, state FROM work_items WHERE kind LIKE 'agent.%' "
        "OR kind LIKE 'pod_ask.%'"
    )
    assert left == [], "nothing left"


@pytest.mark.drives_work
async def test_two_dispatchers_never_run_two_turns_of_one_conversation(
    pod: TurnPod, model_latency
) -> None:
    """Every conversation gets a second turn item under the same key -- what a
    rolling deploy overlapping a cutover seed looks like -- and two dispatchers
    race for all of them. The key must hold them to one at a time."""
    model_latency(300)
    conversations = [await pod.conversation([script_text("ok")]) for _ in range(12)]
    runs = [(c, (await pod.append(c))["agent_run_id"]) for c in conversations]
    async with runtime_sessions()() as session:
        for conversation, run_id in runs:
            await session.execute(
                text_insert_duplicate(), {"run": run_id, "conversation": conversation}
            )
        await session.commit()

    registry = turn_registry()
    first = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="a", slots=4
    )
    second = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="b", slots=4
    )
    violations: list[tuple] = []
    peak = 0

    async def sample() -> None:
        nonlocal peak
        while True:
            found = await rows(
                "SELECT concurrency_key, count(*) FROM work_items "
                "WHERE state = 'running' AND kind = 'agent.turn' "
                "GROUP BY concurrency_key"
            )
            peak = max([peak, *(n for _key, n in found)])
            violations.extend(row for row in found if row[1] > 1)
            await _pace(0.02)

    async def pump(dispatcher) -> None:
        while True:
            claimed = await dispatcher.claim_once()
            if not claimed:
                if not await scalar(
                    "SELECT count(*) FROM work_items WHERE kind = 'agent.turn'"
                ):
                    return
                await _pace(0.05)
                continue
            await asyncio.gather(*(dispatcher.execute(item) for item in claimed))

    sampler = asyncio.create_task(sample())
    try:
        await asyncio.wait_for(asyncio.gather(pump(first), pump(second)), 120)
    finally:
        sampler.cancel()
    assert violations == []
    assert peak == 1
    for _conversation, run_id in runs:
        assert await pod.run_status(run_id) == "COMPLETED"


def text_insert_duplicate():
    from sqlalchemy import text

    return text(
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
        "concurrency_key, concurrency_limit) "
        "SELECT gen_random_uuid(), ns, class, kind, payload, dedup_key || '/twin', "
        "concurrency_key, concurrency_limit FROM work_items "
        "WHERE dedup_key = 'agent-run:' || :run "
        "AND concurrency_key = 'conversation:' || :conversation"
    )


@pytest.mark.drives_work
async def test_a_stop_that_beats_the_claim_ends_the_run_with_no_harness_call(
    pod: TurnPod,
) -> None:
    conversation = await pod.conversation([script_text("should never be said")])
    run_id = (await pod.append(conversation))["agent_run_id"]

    stopped = await pod.stop(conversation)

    assert stopped["status"] == "STOPPED"
    assert await pod.run_status(run_id) == "STOPPED"
    assert await _items("agent.turn") == 0, "the queued turn went with the stop"
    # The stop is a finish like any other: its follow-up was written with it.
    assert await _items("agent.followup") == 1
    await drain(turn_registry())
    roles = {m["role"] for m in await pod.messages(conversation)}
    assert roles == {"user"}, "the harness never ran"


@pytest.mark.drives_work
async def test_a_stop_that_loses_to_the_claim_ends_the_run_at_its_next_check(
    pod: TurnPod, model_latency
) -> None:
    model_latency(3000)
    conversation = await pod.conversation(
        [script_tool_call("pod_tables", {}, tool_call_id="t1"), script_text("late")]
    )
    run_id = (await pod.append(conversation))["agent_run_id"]
    registry = turn_registry()
    dispatcher = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="w"
    )
    [item] = await dispatcher.claim_once()
    running = asyncio.create_task(dispatcher.execute(item))
    await asyncio.sleep(0.5)

    stopped = await pod.stop(conversation)

    assert stopped["status"] == "STOP_REQUESTED"
    await asyncio.wait_for(running, 30)
    assert await pod.run_status(run_id) == "STOPPED"
    assert await _items("agent.turn") == 0


class _Deadlock(Exception):
    sqlstate = "40P01"


def _interrupting_runner(interrupt: BaseException):
    """A real runner whose first finalize is cut after writing everything.

    The run's end and all its follow-ups are written in the fenced transaction;
    ``interrupt`` then ends it before the commit -- a worker dying there, or a
    transient database failure.
    """
    left = {"n": 1}

    def runner(fences: TurnFences) -> TurnRunner:
        real = AgentRunnerService(
            uow_factory=worker_uow_factory(),
            harness_registry=build_harness_registry(),
            terminal_units=fences.terminal,
            step_units=fences.steps,
        )
        staged = real.finalizer.stage_followups

        async def cut_once(uow, **kwargs):
            await staged(uow, **kwargs)
            if left["n"]:
                left["n"] -= 1
                raise interrupt

        real.finalizer.stage_followups = cut_once
        return real

    return runner


async def _answers(pod: TurnPod, conversation: str) -> list[str]:
    return [
        m["text"]
        for m in await pod.messages(conversation)
        if m["role"] == "assistant" and m["kind"] == "TEXT"
    ]


async def _followups_of(run_id: str) -> int:
    return await scalar(
        "SELECT count(*) FROM work_items WHERE dedup_key = :k",
        k=f"followup:{run_id}",
    )


@pytest.mark.drives_work
async def test_a_worker_dying_mid_finalize_commits_nothing_and_finalizes_once(
    pod: TurnPod,
) -> None:
    """Killed between the run's end and its follow-ups: neither survives, and
    the reclaimed turn finishes the run with its follow-ups exactly once."""
    conversation = await pod.conversation([script_text("answered")])
    run_id = (await pod.append(conversation))["agent_run_id"]
    registry = turn_registry(
        runner_factory=_interrupting_runner(asyncio.CancelledError())
    )
    dispatcher = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="dead"
    )
    [item] = await dispatcher.claim_once()
    await dispatcher.execute(item)

    assert await pod.run_status(run_id) == "RUNNING", "the end rolled back"
    assert await _followups_of(run_id) == 0, "and its follow-ups with it"
    # The worker is gone: its lease runs out and another worker reclaims it.
    await write(
        "UPDATE work_items SET lease_until = now() - interval '1 second' "
        "WHERE dedup_key = :k",
        k=f"agent-run:{run_id}",
    )
    reaper = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="next"
    )
    await reaper.reap()
    [(lease_losses, attempts)] = await rows(
        "SELECT lease_losses, attempts FROM work_items WHERE dedup_key = :k",
        k=f"agent-run:{run_id}",
    )
    assert (lease_losses, attempts) == (1, 0)
    await write("UPDATE work_items SET run_at = now() WHERE state = 'queued'")
    await drain(registry, classes=[WorkClass.INTERACTIVE])

    assert await pod.run_status(run_id) == "COMPLETED"
    assert await _followups_of(run_id) == 1, "written exactly once"
    # The answer had committed before the finalize was cut: the reclaimed turn
    # ends the run on it rather than asking the model for another.
    assert await _answers(pod, conversation) == ["answered"]


@pytest.mark.drives_work
async def test_a_transient_failure_mid_finalize_retries_from_the_last_step(
    pod: TurnPod,
) -> None:
    """A deadlock in the finalize is an interruption, not the run failing."""
    conversation = await pod.conversation([script_text("answered")])
    run_id = (await pod.append(conversation))["agent_run_id"]
    deadlock = OperationalError("UPDATE agent_runs", {}, _Deadlock("deadlock"))
    registry = turn_registry(
        runner_factory=_interrupting_runner(deadlock),
        retry=RetryPolicy(max_attempts=3),
    )
    dispatcher = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="w"
    )
    [item] = await dispatcher.claim_once()
    await dispatcher.execute(item)

    assert await pod.run_status(run_id) == "RUNNING"
    assert await _followups_of(run_id) == 0
    [(attempts, state)] = await rows(
        "SELECT attempts, state FROM work_items WHERE dedup_key = :k",
        k=f"agent-run:{run_id}",
    )
    assert (attempts, state) == (1, "queued")

    await write("UPDATE work_items SET run_at = now() WHERE state = 'queued'")
    await drain(registry, classes=[WorkClass.INTERACTIVE])

    assert await pod.run_status(run_id) == "COMPLETED"
    assert await _followups_of(run_id) == 1
    assert await _answers(pod, conversation) == ["answered"]


@pytest.mark.drives_work
async def test_a_long_silent_step_keeps_its_lease(pod: TurnPod, model_latency) -> None:
    """A model request that says nothing for many leases is not a dead worker.

    Scaled down: a 3-second lease and a 12-second silent model request is the
    same as a 60-second lease and a four-minute tool. The reaper runs all along.
    """
    model_latency(12_000)
    conversation = await pod.conversation([script_text("still here")])
    run_id = (await pod.append(conversation))["agent_run_id"]
    registry = turn_registry(lease=timedelta(seconds=3))
    dispatcher = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="w"
    )
    reaper = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="r"
    )
    [item] = await dispatcher.claim_once()
    started = time.monotonic()
    running = asyncio.create_task(dispatcher.execute(item))
    reaped = 0
    while not running.done():
        reaped += await reaper.reap() or 0
        await asyncio.wait({running}, timeout=0.5)
    await running

    assert time.monotonic() - started >= 12
    assert reaped == 0
    assert await pod.run_status(run_id) == "COMPLETED"


@pytest.mark.drives_work
async def test_racing_children_and_their_parent_deliver_each_result_once(
    pod: TurnPod,
) -> None:
    """Children finishing alongside the parent that spawned them, many times.

    Each finish writes its delivery as its own item, which locks only the
    conversation it starts a turn in. Run on two dispatchers at once, every
    result reaches the parent exactly once and nothing deadlocks.
    """
    from app.modules.agent.tests.e2e.subagent_fixtures import (
        finished_children,
    )

    rounds = 10
    delivered = 0
    for _ in range(rounds):
        parent, children = await finished_children(pod, count=4)
        registry = turn_registry()
        dispatchers = [
            dispatcher_for(
                runtime_sessions(), cls, registry=registry, owner=f"{cls.value}-{n}"
            )
            for n in range(2)
            for cls in (WorkClass.EVENTS,)
        ]

        async def pump(dispatcher) -> None:
            for _ in range(200):
                claimed = await dispatcher.claim_once()
                if not claimed:
                    return
                await asyncio.gather(*(dispatcher.execute(i) for i in claimed))

        await asyncio.wait_for(asyncio.gather(*(pump(d) for d in dispatchers)), 60)
        results = await scalar(
            "SELECT count(*) FROM agent_messages WHERE conversation_id = :c "
            "AND message_metadata->>'source' = 'subagent_result'",
            c=UUID(parent),
        )
        assert results == len(children)
        delivered += results
        assert (
            await scalar(
                "SELECT count(*) FROM work_items WHERE state = 'dead' "
                "AND kind LIKE 'agent.%'"
            )
            == 0
        )
    assert delivered == rounds * 4

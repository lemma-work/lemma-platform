"""A turn whose lease was taken over writes nothing more, and its run answers once.

The lease alone does not make a turn's effects exactly-once: a worker can lose
its lease while it is still running the turn -- its heartbeat could not reach
the database -- and keep going. Each step it commits after that is a second
copy of what the worker now holding the turn writes. What fences it is the
claim's epoch, checked under the item's row lock in the same transaction as
every step (``FencedSteps``) and as the run's end (``FencedTerminal``).

Forced deterministically here: the first worker is inside its final model
request when its lease expires; the reaper requeues the turn and a second
claim resumes it while the first is still running. Once with the second claim
in another process, once in the same one -- the owner string then matches, and
only the epoch tells the two runs apart.

The other shape -- the answer committed, then the finalize was lost -- is in
``test_agent_turn_work_e2e.py``: the resumed turn ends the run on that answer
instead of asking the model again.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.core.infrastructure.work import JobContext, WorkClass
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    rows,
    runtime_sessions,
    scalar,
    turn_pod,
    turn_registry,
    write,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

#: Long enough that the second claim starts while the first is inside its
#: model request, short enough to keep the test quick.
MODEL_LATENCY_MS = 1500


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _answers(run_id: str) -> list[str]:
    return [
        text
        for (text,) in await rows(
            "SELECT text FROM agent_messages WHERE agent_run_id = CAST(:run AS uuid) "
            "AND role = 'assistant' AND kind = 'TEXT' ORDER BY sequence",
            run=run_id,
        )
    ]


async def _returns(run_id: str) -> int:
    return await scalar(
        "SELECT count(*) FROM agent_messages WHERE agent_run_id = CAST(:run AS uuid) "
        "AND kind = 'TOOL_RETURN'",
        run=run_id,
    )


@pytest.mark.parametrize("second_owner", ["b", "a"], ids=["other_process", "same"])
async def test_a_turn_that_lost_its_lease_mid_answer_writes_no_second_answer(
    pod: TurnPod, monkeypatch, second_owner: str
) -> None:
    monkeypatch.setattr(settings, "e2e_mock_llm_latency_ms", MODEL_LATENCY_MS)
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("the answer"),
        ]
    )
    run_id = (await pod.append(conversation))["agent_run_id"]
    key = f"agent-run:{run_id}"
    registry = turn_registry()
    first = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="a"
    )
    [item] = await first.claim_once()
    zombie = asyncio.create_task(first.execute(item))

    # The tool step is committed: the first worker is now inside the model
    # request that produces the answer.
    await eventually(
        label="the first worker's tool step committed",
        probe=lambda: _returns(run_id),
        done=lambda n: n == 1,
        interval_seconds=0.05,
    )
    # Its lease lapses (a heartbeat that could not reach the database), the
    # reaper requeues the turn, and a second claim resumes it at once.
    await write(
        "UPDATE work_items SET lease_until = now() - interval '1 second' "
        "WHERE dedup_key = :k",
        k=key,
    )
    second = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner=second_owner
    )
    assert await second.reap() == 1
    await write("UPDATE work_items SET run_at = now() WHERE dedup_key = :k", k=key)
    [again] = await second.claim_once()
    assert again.id == item.id and again.lease_epoch == item.lease_epoch + 1
    resumed = asyncio.create_task(second.execute(again))

    zombie_outcome, resumed_outcome = await asyncio.wait_for(
        asyncio.gather(zombie, resumed), 60
    )

    assert zombie_outcome == "lease_lost"
    assert resumed_outcome == "done"
    assert await pod.run_status(run_id) == "COMPLETED"
    assert await _answers(run_id) == ["the answer"], "one answer, not two"
    assert await _returns(run_id) == 1
    assert (
        await scalar("SELECT count(*) FROM work_items WHERE dedup_key = :k", k=key) == 0
    )


async def test_a_turn_whose_lease_lapsed_mid_tool_starts_no_further_tool(
    pod: TurnPod, monkeypatch
) -> None:
    """A worker that can no longer vouch for its lease runs no next tool.

    The steps are fenced, but a tool's effects mostly are not (a pod record, a
    Slack message), so the turn asks its lease before every tool call. Here
    the local clock says the lease lapsed while the first tool ran -- what a
    heartbeat that cannot reach the database leads to -- and the second call
    must not start. The turn stops as a lost lease, uncharged and RUNNING, and
    the worker that next claims it runs the second tool once.
    """
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="first-1"),
            script_tool_call("pod_tables", {}, tool_call_id="second-1"),
            script_text("the answer"),
        ]
    )
    run_id = (await pod.append(conversation))["agent_run_id"]
    key = f"agent-run:{run_id}"
    asked: list[bool] = []
    lapsed = True

    def lease_alive(ctx: JobContext) -> bool:
        # The first ask is the first tool's; every later one is after it.
        alive = not (lapsed and asked)
        asked.append(alive)
        return alive

    monkeypatch.setattr(JobContext, "lease_alive", lease_alive)
    registry = turn_registry()
    first = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="a"
    )
    [item] = await first.claim_once()

    assert await asyncio.wait_for(first.execute(item), 60) == "lease_lost"
    assert asked == [True, False]
    returns = await rows(
        "SELECT tool_call_id FROM agent_messages WHERE agent_run_id = "
        "CAST(:run AS uuid) AND kind = 'TOOL_RETURN'",
        run=run_id,
    )
    assert [call for (call,) in returns] == ["first-1"], "the second tool never ran"
    assert await pod.run_status(run_id) == "RUNNING"
    assert (
        await scalar("SELECT attempts FROM work_items WHERE dedup_key = :k", k=key) == 0
    )

    # The lease runs out in the database, and the next claim finishes the turn.
    lapsed = False
    await write(
        "UPDATE work_items SET lease_until = now() - interval '1 second' "
        "WHERE dedup_key = :k",
        k=key,
    )
    second = dispatcher_for(
        runtime_sessions(), WorkClass.INTERACTIVE, registry=registry, owner="b"
    )
    assert await second.reap() == 1
    await write("UPDATE work_items SET run_at = now() WHERE dedup_key = :k", k=key)
    [again] = await second.claim_once()
    assert await asyncio.wait_for(second.execute(again), 60) == "done"

    assert await pod.run_status(run_id) == "COMPLETED"
    assert await _answers(run_id) == ["the answer"]
    counts = dict(
        await rows(
            "SELECT tool_call_id, count(*) FROM agent_messages WHERE agent_run_id = "
            "CAST(:run AS uuid) AND kind = 'TOOL_RETURN' GROUP BY 1",
            run=run_id,
        )
    )
    assert counts == {"first-1": 1, "second-1": 1}

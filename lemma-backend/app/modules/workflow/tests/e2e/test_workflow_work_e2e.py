"""Workflow runs on the work runtime: every suspension writes what ends it.

Runs are started and forms submitted over the API; everything between is work
items the runtime claims and executes (``workflow_work_support.py``). Each test
asserts the items a transition wrote and what running them did, so a wait that
commits without the work that wakes it -- what the reconciliation sweep used to
paper over -- shows up here as a run that never moves.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import text

from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for, make_due
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.tests.e2e.workflow_work_support import FlowWork, flow_work

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

FORM_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


@pytest.fixture
async def flows(
    authenticated_client, fixed_test_org, fixed_test_user, db_manager
) -> AsyncIterator[FlowWork]:
    async with flow_work(
        authenticated_client,
        db_manager,
        org_id=fixed_test_org["id"],
        user_id=fixed_test_user["id"],
    ) as work:
        yield work


def four_steps(function_name: str, member_id: str) -> tuple[list[dict], list[dict]]:
    """FUNCTION -> TIME -> DECISION -> HUMAN -> end."""
    nodes = [
        {
            "id": "fetch",
            "type": "FUNCTION",
            "config": {"function_name": function_name, "input_mapping": {}},
        },
        {"id": "pause", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 1}},
        {
            "id": "triage",
            "type": "DECISION",
            "config": {
                "question": {
                    "instruction": "Should this go to a person?",
                    "evidence": {"type": "expression", "value": "fetch"},
                    "answer": {
                        "type": "string",
                        "enum": ["yes", "no"],
                        "description": "Does a person need to approve this?",
                    },
                    "routes": {"yes": "approve"},
                }
            },
        },
        {
            "id": "approve",
            "type": "FORM",
            "label": "Approve the refund",
            "config": {
                "input_schema": FORM_SCHEMA,
                "assignee_pod_member_id": member_id,
            },
        },
        {"id": "end", "type": "END"},
    ]
    edges = [
        {"id": "e1", "source": "fetch", "target": "pause"},
        {"id": "e2", "source": "pause", "target": "triage"},
        {"id": "e3", "source": "triage", "target": "end"},
        {"id": "e4", "source": "approve", "target": "end"},
    ]
    return nodes, edges


async def _items(flows: FlowWork, kind: str) -> int:
    return int(
        await flows.scalar(  # type: ignore[arg-type]
            "SELECT count(*) FROM work_items WHERE kind = :kind", kind=kind
        )
    )


async def _expiries(flows: FlowWork, *, resolved: bool) -> int:
    """Queued expiry items whose wait is (or is not) still active."""
    return int(
        await flows.scalar(  # type: ignore[arg-type]
            "SELECT count(*) FROM work_items i JOIN workflow_run_waits w"
            " ON w.id = CAST(i.payload->>'wait_id' AS uuid)"
            " WHERE i.kind = 'workflow.wait_expire' AND i.state = 'queued'"
            " AND (w.status <> 'ACTIVE') = :resolved",
            resolved=resolved,
        )
    )


async def test_a_function_time_decision_form_flow_runs_on_work_items(
    flows: FlowWork,
) -> None:
    function = await flows.function("complete")
    workflow = await flows.workflow(*four_steps(function, await flows.member_id()))

    run = await flows.start(workflow)
    # FUNCTION: the run and the item that starts it, plus the wait's expiry,
    # all committed by the request that started the workflow.
    assert run["active_wait"]["wait_type"] == "FUNCTION"
    assert await _items(flows, "function.run") == 1
    assert await _items(flows, "workflow.wait_expire") == 1

    await flows.drain()
    await eventually(
        label="the function's end woke the run onto its timer",
        probe=lambda: _drained_run(flows, run["id"]),
        done=lambda current: (
            current["active_wait"] and current["active_wait"]["wait_type"] == "TIME"
        ),
    )
    # TIME: one resume item, due at the wait's own time and not before.
    assert await _items(flows, "workflow.resume") == 1
    # The FUNCTION wait resolved, so its expiry went with it.
    assert await _expiries(flows, resolved=True) == 0
    await flows.drain()
    assert (await flows.run(run["id"]))["active_wait"]["wait_type"] == "TIME"

    await eventually(
        label="the timer fired and the decision was asked and routed",
        probe=lambda: _drained_run(flows, run["id"]),
        done=lambda current: current["status"] == "WAITING",
        timeout_seconds=15,
    )
    current = await flows.run(run["id"])
    assert current["active_wait"]["wait_type"] == "HUMAN"
    assert current["execution_context"]["triage"]["answer"] == "yes"
    assert flows.decisions.asked == 1
    # HUMAN: the form is a line waiting on the assignee, in their own channel.
    assert await _items(flows, "workflow.notify_form") == 0
    assert (
        await flows.scalar(
            "SELECT count(*) FROM channel_questions WHERE kind = 'FORM'"
            " AND ref LIKE :ref",
            ref=f"wf:{run['id']}:%",
        )
        == 1
    )
    # Named by the label its author gave the step, not only the workflow's.
    said = await flows.scalar(
        "SELECT m.body FROM channel_questions q"
        " JOIN channel_messages m ON m.id = q.message_id"
        " WHERE q.kind = 'FORM' AND q.ref LIKE :ref",
        ref=f"wf:{run['id']}:%",
    )
    assert "Approve the refund" in str(said)

    assert await _expiries(flows, resolved=False) == 1

    done = await flows.submit(run["id"], "approve", {"ok": True})
    assert done["status"] == "COMPLETED"
    assert flows.runtime.invocations.total() == 1
    # No expiry is left queued for a wait that already resolved.
    assert await _expiries(flows, resolved=True) == 0
    assert await _items(flows, "workflow.wait_expire") == 0


async def _drained_run(flows: FlowWork, run_id: str) -> dict:
    await flows.drain()
    return await flows.run(run_id)


async def test_a_function_step_that_never_answers_is_expired_and_its_run_cancelled(
    flows: FlowWork,
) -> None:
    function = await flows.function("accept")
    workflow = await flows.workflow(
        [
            {
                "id": "fetch",
                "type": "FUNCTION",
                "config": {"function_name": function, "input_mapping": {}},
            },
            {"id": "end", "type": "END"},
        ],
        [{"id": "e1", "source": "fetch", "target": "end"}],
    )
    run = await flows.start(workflow)
    function_run = run["active_wait"]["external_ref"]
    await flows.drain()

    # Its ceiling passes (aged, not waited out).
    async with flows.sessions() as session:  # type: ignore[operator]
        await session.execute(
            text(
                "UPDATE workflow_run_waits SET created_at = now() - interval '7 days' "
                "WHERE run_id = :run"
            ),
            {"run": UUID(run["id"])},
        )
        await session.commit()
    await make_due(flows.sessions, kind="workflow.wait_expire")  # type: ignore[arg-type]
    await flows.drain()

    current = await flows.run(run["id"])
    assert current["status"] == "FAILED"
    assert "did not finish within" in current["error"]
    assert (
        await flows.scalar(
            "SELECT status FROM function_runs WHERE id = :id", id=UUID(function_run)
        )
        == "CANCELLED"
    )
    # The sandbox was asked to stop the run nobody is waiting for.
    assert flows.runtime.cancels[function_run] == 1


async def test_an_early_expiry_item_re_arms_for_the_real_ceiling(
    flows: FlowWork,
) -> None:
    workflow = await flows.workflow(
        [
            {"id": "approve", "type": "FORM", "config": {"input_schema": FORM_SCHEMA}},
            {"id": "end", "type": "END"},
        ],
        [{"id": "e1", "source": "approve", "target": "end"}],
    )
    run = await flows.start(workflow)
    await make_due(flows.sessions, kind="workflow.wait_expire")  # type: ignore[arg-type]
    await flows.drain()

    assert (await flows.run(run["id"]))["status"] == "WAITING"
    due_in_days = await flows.scalar(
        "SELECT extract(epoch FROM run_at - now()) / 86400 FROM work_items "
        "WHERE kind = 'workflow.wait_expire'"
    )
    assert 2.5 < float(due_in_days) <= 3.0  # type: ignore[arg-type]


async def test_runs_finishing_together_admit_exactly_the_room_they_free(
    flows: FlowWork,
) -> None:
    workflow = await flows.workflow(
        [
            {"id": "pause", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 3600}},
            {"id": "end", "type": "END"},
        ],
        [{"id": "e1", "source": "pause", "target": "end"}],
        limit=2,
    )
    runs = [await flows.start(workflow) for _ in range(5)]
    waits = [run["active_wait"]["wait_type"] for run in runs]
    assert waits == ["TIME", "TIME", "QUEUED", "QUEUED", "QUEUED"]
    # Setting the limit wrote an admission pass; with no room it admits nobody.
    await flows.drain()
    assert [
        (await flows.run(run["id"]))["active_wait"]["wait_type"] for run in runs
    ] == waits

    # Both admitted runs' timers fire at once, on two dispatchers.
    await make_due(flows.sessions, kind="workflow.resume")  # type: ignore[arg-type]
    first, second = (
        dispatcher_for(
            flows.sessions,  # type: ignore[arg-type]
            WorkClass.EVENTS,
            registry=flows.registry,
            owner=owner,
            slots=1,
            quantum=1,
        )
        for owner in ("a", "b")
    )
    claimed = [*(await first.claim_once()), *(await second.claim_once())]
    assert len(claimed) == 2
    await asyncio.gather(first.execute(claimed[0]), second.execute(claimed[1]))

    statuses = [(await flows.run(run["id"])) for run in runs]
    assert [s["status"] for s in statuses[:2]] == ["COMPLETED", "COMPLETED"]
    queued = [
        s["active_wait"]["wait_type"] if s["active_wait"] else None
        for s in statuses[2:]
    ]
    # Two places freed, two parked runs admitted -- oldest first -- and one
    # still waits its turn.
    assert queued == ["TIME", "TIME", "QUEUED"]


async def test_a_requested_start_runs_once_however_often_it_is_delivered(
    flows: FlowWork,
) -> None:
    from uuid import uuid7

    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.workflow.contracts.run_work import (
        WorkflowStartRequest,
        WorkflowTrigger,
        enqueue_workflow_start,
    )

    workflow = await flows.workflow([{"id": "end", "type": "END"}], [])
    flow_id = await flows.scalar(
        "SELECT id FROM workflow_flows WHERE name = :name", name=workflow
    )
    run_id = uuid7()
    request = WorkflowStartRequest(
        flow_id=flow_id,  # type: ignore[arg-type]
        user_id=flows.user_id,
        run_id=run_id,
        trigger=WorkflowTrigger(payload={"order": 7}),
    )
    for _ in range(2):
        async with flows.sessions() as session:  # type: ignore[operator]
            uow = SqlAlchemyUnitOfWork(session)
            await enqueue_workflow_start(
                session, pod_id=UUID(flows.pod_id), request=request
            )
            await uow.commit()
        await flows.drain()

    current = await flows.run(str(run_id))
    assert current["status"] == "COMPLETED"
    assert current["execution_context"]["start"]["payload"] == {"order": 7}
    assert (
        await flows.scalar(
            "SELECT count(*) FROM workflow_flow_runs WHERE flow_id = :flow",
            flow=flow_id,
        )
        == 1
    )

"""The cutover: runs and waits in flight at deploy get the items that now drive them.

Every state the old path left behind is built through the API and then has its
work items deleted -- which is exactly what a row written by the previous
release looks like -- before the migration's statements run. Each must then
move on its own, and running the statements twice (the migration overlapping a
release still running the old code) must write nothing twice.
"""

from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from app.core.infrastructure.work.testing import make_due
from app.modules.test_support.e2e.waiters import eventually
from app.modules.workflow.tests.e2e.test_workflow_work_e2e import FORM_SCHEMA
from app.modules.workflow.tests.e2e.workflow_work_support import FlowWork, flow_work

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _seed_statements() -> list[str]:
    path = (
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-14_change_log_and_flows_0055.py"
    )
    spec = importlib.util.spec_from_file_location("change_log_and_flows_0055", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.flow_seed_statements()


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


async def _execute(flows: FlowWork, sql: str, **params: object) -> None:
    async with flows.sessions() as session:  # type: ignore[operator]
        await session.execute(text(sql), params)
        await session.commit()


def _single(node: dict) -> tuple[list[dict], list[dict]]:
    return [node, {"id": "end", "type": "END"}], [
        {"id": "e1", "source": node["id"], "target": "end"}
    ]


async def test_the_cutover_arms_every_wait_and_ends_every_function_run(
    flows: FlowWork,
) -> None:
    flows.runtime.on_accepted = None  # functions end only when told to
    completes = await flows.function("complete")
    never = await flows.function("accept")
    function_step = await flows.workflow(
        *_single(
            {
                "id": "fetch",
                "type": "FUNCTION",
                "config": {"function_name": completes, "input_mapping": {}},
            }
        )
    )
    running_step = await flows.workflow(
        *_single(
            {
                "id": "fetch",
                "type": "FUNCTION",
                "config": {"function_name": never, "input_mapping": {}},
            }
        )
    )
    timer = await flows.workflow(
        *_single(
            {"id": "pause", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 1}}
        )
    )
    form = await flows.workflow(
        *_single({"id": "ask", "type": "FORM", "config": {"input_schema": FORM_SCHEMA}})
    )
    # Held by a timer, not a form: a run still on its entry form holds no
    # place in the limit.
    capped = await flows.workflow(
        *_single(
            {"id": "hold", "type": "WAIT_UNTIL", "config": {"timeout_seconds": 3600}}
        ),
        limit=1,
    )

    queued_fn = await flows.start(function_step)  # function run PENDING
    lost_fn = await flows.start(function_step)  # function run ends, resume lost
    started_fn = await flows.start(running_step)  # function run RUNNING
    timed = await flows.start(timer)
    asked = await flows.start(form)
    first_capped = await flows.start(capped)
    parked = await flows.start(capped)
    assert parked["active_wait"]["wait_type"] == "QUEUED"
    # The other release's work: one function run started, one finished.
    lost_ref = lost_fn["active_wait"]["external_ref"]
    started_ref = started_fn["active_wait"]["external_ref"]
    await _execute(
        flows,
        "UPDATE function_runs SET status = 'COMPLETED', output_data = '{\"ok\": 1}', "
        "completed_at = now() WHERE id = :id",
        id=UUID(lost_ref),
    )
    await _execute(
        flows,
        "UPDATE function_runs SET status = 'RUNNING', started_at = now(), "
        "deadline_at = now() - interval '10 minutes' WHERE id = :id",
        id=UUID(started_ref),
    )
    # ...and the room the parked run is waiting for, freed without admitting it.
    await _execute(
        flows,
        "UPDATE workflow_flow_runs SET status = 'CANCELLED' WHERE id = :id",
        id=UUID(first_capped["id"]),
    )
    await _execute(
        flows,
        "UPDATE workflow_run_waits SET status = 'CANCELLED' WHERE run_id = :id",
        id=UUID(first_capped["id"]),
    )
    # Written by the previous release: nothing in the work table.
    await _execute(flows, "DELETE FROM work_items")

    for statement in _seed_statements():
        await _execute(flows, statement)
    seeded = await flows.scalar("SELECT count(*) FROM work_items")
    for statement in _seed_statements():
        await _execute(flows, statement)
    assert await flows.scalar("SELECT count(*) FROM work_items") == seeded

    flows.runtime.on_accepted = None
    await flows.drain()

    # In flight on the old path: never started again, failed saying why, and
    # each workflow waiting on one hears that it ended.
    for fn, ref in (
        (queued_fn, queued_fn["active_wait"]["external_ref"]),
        (started_fn, started_fn["active_wait"]["external_ref"]),
    ):
        assert flows.runtime.invocations[ref] == 0
        ended = await flows.scalar(
            "SELECT status || ': ' || error FROM function_runs WHERE id = :id",
            id=UUID(ref),
        )
        assert ended.startswith("FAILED: Interrupted by the upgrade"), ended
        assert (await flows.run(fn["id"]))["status"] == "FAILED"
    # The function run whose end the old consumer never delivered: resumed.
    assert (await flows.run(lost_fn["id"]))["status"] == "COMPLETED"
    # The timer fires at its time.
    await eventually(
        label="the seeded timer fired",
        probe=lambda: _drained_status(flows, timed["id"]),
        done=lambda status: status == "COMPLETED",
    )
    # The form keeps waiting, its expiry re-armed for its real ceiling.
    assert (await flows.run(asked["id"]))["status"] == "WAITING"
    assert (
        await flows.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'workflow.wait_expire' "
            "AND run_at > now() + interval '1 day'"
        )
        >= 1
    )
    # The parked run is admitted into the room that was freed.
    admitted = await flows.run(parked["id"])
    # (The drain pulls its timer forward, so it runs to the end.)
    assert admitted["status"] == "COMPLETED"
    assert [step["node_id"] for step in admitted["step_history"]] == ["hold", "end"]


async def _drained_status(flows: FlowWork, run_id: str) -> str:
    await make_due(flows.sessions, kind="workflow.resume")  # type: ignore[arg-type]
    await flows.drain()
    return (await flows.run(run_id))["status"]

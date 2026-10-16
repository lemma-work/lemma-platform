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


async def test_every_run_and_wait_in_flight_at_the_cutover_moves_on(
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
    capped = await flows.workflow(
        *_single(
            {"id": "ask", "type": "FORM", "config": {"input_schema": FORM_SCHEMA}}
        ),
        limit=1,
    )

    queued_fn = await flows.start(function_step)  # function run PENDING
    stale_fn = await flows.start(function_step)  # queued hours before the release
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
    stale_ref = stale_fn["active_wait"]["external_ref"]
    await _execute(
        flows,
        "UPDATE function_runs SET created_at = now() - interval '2 hours' "
        "WHERE id = :id",
        id=UUID(stale_ref),
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

    # Queued function run: started by its seeded item, once.
    queued_ref = queued_fn["active_wait"]["external_ref"]
    assert flows.runtime.invocations[queued_ref] == 1
    # Queued long before the release: not started by the cutover, failed
    # saying why.
    assert flows.runtime.invocations[stale_ref] == 0
    stale_run = await flows.scalar(
        "SELECT status || ': ' || error FROM function_runs WHERE id = :id",
        id=UUID(stale_ref),
    )
    assert stale_run.startswith("FAILED: Not started before the upgrade"), stale_run
    # The function run whose end the old consumer never delivered: resumed.
    assert (await flows.run(lost_fn["id"]))["status"] == "COMPLETED"
    # The one an old worker started: never invoked again, failed at its deadline.
    assert flows.runtime.invocations[started_ref] == 0
    assert (
        await flows.scalar(
            "SELECT status FROM function_runs WHERE id = :id", id=UUID(started_ref)
        )
        == "FAILED"
    )
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
    assert admitted["active_wait"]["wait_type"] == "HUMAN"


async def _drained_status(flows: FlowWork, run_id: str) -> str:
    await make_due(flows.sessions, kind="workflow.resume")  # type: ignore[arg-type]
    await flows.drain()
    return (await flows.run(run_id))["status"]

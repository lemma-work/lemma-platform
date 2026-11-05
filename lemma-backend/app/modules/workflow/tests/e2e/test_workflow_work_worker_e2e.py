"""A FUNCTION -> TIME -> DECISION -> HUMAN workflow through a real worker process.

The API starts the run and submits the form, in this process; the worker --
``python -m app.worker`` with the sandbox and the decision provider replaced at
their seams (``function_work_entrypoint``) -- does everything between, from the
items each transition wrote. Nothing is pumped by the test: if a suspension
committed without the work that ends it, the run would sit there.

Also the timer's accuracy: a wait-until fires within a poll interval and a claim
of its due time, not on a sweep's schedule.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID

import pytest

from app.modules.function.tests.e2e.function_work_support import (
    FakeRuntime,
    callback_settler,
    fake_runtime,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process
from app.modules.workflow.tests.e2e.test_workflow_work_e2e import four_steps
from app.modules.workflow.tests.e2e.workflow_work_support import FlowWork, flow_work

# Slow, and in the protected lane: each starts its own worker subprocess, which
# must not share an e2e process with the session-scoped base worker (both
# would claim the same work items, and the harness flushes the shared Redis).
pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]

#: How late a timer may fire: the worker's poll, a claim and the advance.
TIMER_SLACK_SECONDS = 1.5


@pytest.fixture
async def runtime(db_manager) -> AsyncIterator[FakeRuntime]:
    async with fake_runtime() as served:
        served.on_accepted = callback_settler(db_manager.session_factory)
        yield served


async def test_a_four_step_workflow_runs_end_to_end_on_a_real_worker(
    e2e_settings,
    db_manager,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    runtime: FakeRuntime,
) -> None:
    async with flow_work(
        authenticated_client,
        db_manager,
        org_id=fixed_test_org["id"],
        user_id=fixed_test_user["id"],
    ) as flows:
        flows.runtime = runtime
        function = await flows.function("complete")
        workflow = await flows.workflow(*four_steps(function, await flows.member_id()))

        async with production_worker_process(
            e2e_settings,
            log_prefix="workflow_work",
            worker_entrypoint="app.modules.function.tests.e2e.function_work_entrypoint",
            extra_env={
                "E2E_FAKE_FUNCTION_RUNTIME_URL": runtime.url,
                "E2E_DECISION_ANSWER": "yes",
                "WORK_POLL_INTERVAL_SECONDS": "0.2",
            },
        ) as worker:
            run = await flows.start(workflow)
            current = await eventually(
                label="the worker took the run to its form",
                probe=lambda: flows.run(run["id"]),
                done=lambda state: state["status"] == "WAITING",
                timeout_seconds=60,
                fail_fast=lambda state: (
                    worker.read_log_tail()
                    if worker.process.poll() is not None or state["status"] == "FAILED"
                    else None
                ),
            )
            assert current["active_wait"]["wait_type"] == "HUMAN"
            assert current["execution_context"]["triage"]["answer"] == "yes"
            assert current["execution_context"]["fetch"]["echo"] == {}
            assert runtime.invocations.total() == 1
            await _assert_timer_fired_on_time(flows, run["id"])
            await eventually(
                label="the assignee was told",
                probe=lambda: flows.scalar(
                    "SELECT count(*) FROM channel_questions WHERE kind = 'FORM'"
                    " AND ref LIKE :ref",
                    ref=f"wf:{run['id']}:%",
                ),
                done=lambda n: n == 1,
            )

            done = await flows.submit(run["id"], "approve", {"ok": True})
            assert done["status"] == "COMPLETED"
        assert (
            await flows.scalar(
                "SELECT count(*) FROM work_items WHERE state <> 'queued' "
                "AND kind LIKE 'workflow.%'"
            )
            == 0
        ), "nothing left running or dead"


async def _assert_timer_fired_on_time(flows: FlowWork, run_id: str) -> None:
    row = await flows.scalar(
        "SELECT json_build_object('due', scheduled_at, 'done', completed_at) "
        "FROM workflow_run_waits WHERE run_id = :run AND wait_type = 'TIME'",
        run=UUID(run_id),
    )
    assert isinstance(row, dict)
    due = datetime.fromisoformat(str(row["due"]))
    fired = datetime.fromisoformat(str(row["done"]))
    late = (fired - due).total_seconds()
    assert 0 <= late < TIMER_SLACK_SECONDS, f"timer fired {late:.3f}s after its time"

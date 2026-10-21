"""SIGKILL a real worker process around each trigger transaction; nothing doubles.

A subprocess claims the item and hangs at the named point inside its
transaction; the test kills it with SIGKILL, Postgres rolls the transaction
back, the lease is expired, and the item runs again in this process:

* a fire killed before its commit leaves no run, then fires exactly once;
* a start killed after creating the agent run but before its commit leaves no
  conversation, then starts exactly one;
* a filter killed during the model call never calls the model a second time.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for, make_due
from app.core.infrastructure.db.session import async_session_maker
from app.modules.schedule.tests.e2e.test_trigger_work_e2e import (
    _deliver,
    _webhook_schedule,
    counting_filter,  # noqa: F401  fixture
)
from app.modules.schedule.tests.e2e.trigger_work_support import (
    create_pod,
    create_time_schedule,
    execute,
    items,
    run,
    runs,
    scalar,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


_BACKEND = Path(__file__).resolve().parents[5]


async def _kill_inside(stage: str, tmp_path: Path) -> Path:
    marker, calls = tmp_path / f"{stage}.reached", tmp_path / f"{stage}.calls"
    # The database this test's own sessions use, whatever the settings say.
    async with async_session_maker() as session:
        bind = session.get_bind()
    database_url = bind.url.render_as_string(hide_password=False)
    log = (tmp_path / f"{stage}.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.modules.schedule.tests.e2e.trigger_crash_entrypoint",
            stage,
            str(marker),
            str(calls),
        ],
        cwd=_BACKEND,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                part for part in (".", os.environ.get("PYTHONPATH")) if part
            ),
            "DATABASE_URL": database_url,
            "ENVIRONMENT": "testing",
        },
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:

        async def reached() -> bool:
            return marker.exists() or process.poll() is not None

        await eventually(
            label=f"{stage} worker reaching its hang",
            probe=reached,
            done=bool,
            timeout_seconds=90,
        )
        assert process.poll() is None, (
            f"{stage} worker exited early: "
            + (tmp_path / f"{stage}.log").read_text()[-2000:]
        )
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
    return calls


async def _expire_and_rerun(*classes: WorkClass) -> None:
    await execute(
        "UPDATE work_items SET lease_until = now() - interval '1 second' "
        "WHERE state = 'running'"
    )
    for cls in classes:
        # Reaped first: the lease loss is charged with a backoff, which the
        # pull to now below then skips.
        assert (
            await dispatcher_for(async_session_maker, cls, owner="reaper").reap() == 1
        )
    await make_due(async_session_maker)
    await run(*classes)


async def _schedule_with_due_fire(
    client: AsyncClient, org_id: str
) -> tuple[UUID, dict]:
    pod_id = await create_pod(client, org_id)
    schedule = await create_time_schedule(client, pod_id)
    await run(WorkClass.EVENTS)  # the pod's own provisioning, out of the way
    await make_due(async_session_maker, kind="schedule.fire")
    return pod_id, schedule


async def test_a_fire_killed_before_its_commit_fires_exactly_once(
    authenticated_client: AsyncClient, fixed_test_org, tmp_path: Path
):
    _, schedule = await _schedule_with_due_fire(
        authenticated_client, fixed_test_org["id"]
    )

    await _kill_inside("fire", tmp_path)

    assert await runs(schedule["id"]) == []
    assert [i.state for i in await items("schedule.fire")] == ["running"]
    assert await items("schedule.start_target") == []

    await _expire_and_rerun(WorkClass.EVENTS)

    assert len(await runs(schedule["id"])) == 1
    assert len(await items("schedule.start_target")) == 1
    [next_fire] = await items("schedule.fire")
    assert next_fire.state == "queued"


async def test_a_start_killed_before_its_commit_starts_the_agent_exactly_once(
    authenticated_client: AsyncClient, fixed_test_org, tmp_path: Path
):
    _, schedule = await _schedule_with_due_fire(
        authenticated_client, fixed_test_org["id"]
    )
    await run(WorkClass.EVENTS)

    await _kill_inside("start", tmp_path)

    [reserved] = await runs(schedule["id"])
    assert (reserved["status"], reserved["attempts"]) == ("PROCESSING", 0)
    conversations = (
        "SELECT count(*) FROM agent_conversations WHERE id = CAST(:id AS uuid)"
    )
    assert await scalar(conversations, id=reserved["target_run_id"]) == 0

    await _expire_and_rerun(WorkClass.AUTOMATION)

    [started] = await runs(schedule["id"])
    assert (started["status"], started["attempts"]) == ("DISPATCHED", 1)
    assert await scalar(conversations, id=started["target_run_id"]) == 1


async def test_a_filter_killed_mid_call_is_asked_once_more_and_fires(
    authenticated_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    tmp_path: Path,
    counting_filter,  # noqa: F811
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(
        pod_id, UUID(fixed_test_user["id"]), filter_instruction="Only real pings"
    )
    await run(WorkClass.EVENTS)
    await _deliver(schedule, "evt-crash")

    calls = await _kill_inside("filter", tmp_path)

    assert calls.read_text().splitlines() == ["evt-crash"]
    [claimed] = await runs(schedule.id)
    assert claimed["status"] == "FILTERING"

    await _expire_and_rerun(WorkClass.AUTOMATION)
    await asyncio.sleep(0)

    # Asked once more by the replay (the verdict had not committed), and no
    # more: the killed worker's call is not repeated a third time.
    assert counting_filter.asked == ["evt-crash"]
    assert calls.read_text().splitlines() == ["evt-crash"]
    [settled] = await runs(schedule.id)
    assert settled["status"] == "DISPATCHED"
    assert settled["metadata"]["filter_asks"] == 2

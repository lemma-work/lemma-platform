"""A schedule's ``max_concurrent_runs``: fires past it are skipped, and say so.

Drives the real fire handler against Postgres. The agent's conversation is
started but never run -- no worker -- so the run it belongs to stays in flight
for exactly as long as the test wants, and its outcome is recorded through the
schedule ledger the way the outcome consumer records it.
"""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.workflow.api.dependencies import build_workflow_engine
from app.modules.workflow.services.schedule_start_service import (
    ScheduleStartService,
)
from app.modules.workflow.tests.e2e.test_flow_execution_e2e import (
    _create_agent,
    _create_pod,
)

pytestmark = [pytest.mark.e2e]


async def _capped_agent_schedule(
    client: AsyncClient, org_id: str, *, cap: int
) -> tuple[str, str]:
    pod_id = await _create_pod(client, org_id, "Run cap")
    agent_name = await _create_agent(client, pod_id)
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "TIME",
            "agent_name": agent_name,
            # Never due during the test; it is fired by hand below.
            "config": {"cron": "0 0 1 1 *", "max_concurrent_runs": cap},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["max_concurrent_runs"] == cap
    return pod_id, response.json()["id"]


async def _fire(schedule_id: str, event_id: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ScheduleStartService(build_workflow_engine(uow)).start_for_fire(
            schedule_id=schedule_id,
            user_id=None,
            payload={},
            schedule_event_id=event_id,
        )
        await uow.commit()


async def _runs(client: AsyncClient, pod_id: str, schedule_id: str) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _finish(run: dict) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        assert await ScheduleRunOutcomeService(uow).record_target_outcome(
            target_kind="AGENT",
            target_run_id=run["target_run_id"],
            status=ScheduleRunStatus.COMPLETED,
            completed_at=None,
        )
        await uow.commit()


async def test_a_fire_past_the_cap_is_skipped_until_a_run_finishes(
    authenticated_client: AsyncClient, fixed_test_org
) -> None:
    pod_id, schedule_id = await _capped_agent_schedule(
        authenticated_client, fixed_test_org["id"], cap=1
    )

    await _fire(schedule_id, f"first-{uuid4()}")
    await _fire(schedule_id, f"second-{uuid4()}")

    runs = await _runs(authenticated_client, pod_id, schedule_id)
    skipped = [run for run in runs if run["status"] == "FILTERED"]
    started = [run for run in runs if run["status"] != "FILTERED"]
    assert [run["status"] for run in started] == ["DISPATCHED"]
    (skip,) = skipped
    assert skip["target_run_id"] is None
    assert skip["metadata"]["skipped_because"] == "max_concurrent_runs"
    assert skip["metadata"]["max_concurrent_runs"] == 1
    schedule = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule_id}")
    ).json()
    assert schedule["last_fire_status"] == "FILTERED"
    # A skip is not a failure: the breaker is left alone.
    assert schedule["consecutive_failures"] == 0

    await _finish(started[0])
    await _fire(schedule_id, f"third-{uuid4()}")

    runs = await _runs(authenticated_client, pod_id, schedule_id)
    assert sorted(run["status"] for run in runs) == [
        "COMPLETED",
        "DISPATCHED",
        "FILTERED",
    ]


async def test_concurrent_fires_cannot_both_take_the_last_place(
    authenticated_client: AsyncClient, fixed_test_org
) -> None:
    """Count-then-claim is a race; the admission lock is what makes it one."""
    pod_id, schedule_id = await _capped_agent_schedule(
        authenticated_client, fixed_test_org["id"], cap=1
    )

    await asyncio.gather(
        *(_fire(schedule_id, f"burst-{index}-{uuid4()}") for index in range(4))
    )

    runs = await _runs(authenticated_client, pod_id, schedule_id)
    assert sorted(run["status"] for run in runs) == [
        "DISPATCHED",
        "FILTERED",
        "FILTERED",
        "FILTERED",
    ]


async def test_a_redelivered_fire_is_not_turned_away_by_a_full_schedule(
    authenticated_client: AsyncClient, fixed_test_org
) -> None:
    """It was admitted once; a redelivery is the claim's to judge, not a skip.

    Full here because the cap was lowered under two runs already in flight.
    """
    pod_id, schedule_id = await _capped_agent_schedule(
        authenticated_client, fixed_test_org["id"], cap=2
    )
    event_id = f"once-{uuid4()}"
    await _fire(schedule_id, event_id)
    await _fire(schedule_id, f"other-{uuid4()}")
    lowered = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule_id}",
        json={"config": {"cron": "0 0 1 1 *", "max_concurrent_runs": 1}},
    )
    assert lowered.status_code == 200, lowered.text

    await _fire(schedule_id, event_id)

    runs = await _runs(authenticated_client, pod_id, schedule_id)
    assert [run["status"] for run in runs] == ["DISPATCHED", "DISPATCHED"]
    schedule = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule_id}")
    ).json()
    assert schedule["last_fire_status"] == "TRIGGERED"


@pytest.mark.parametrize("cap", [0, 1001, "2", True])
async def test_an_invalid_cap_is_refused_on_create_and_update(
    authenticated_client: AsyncClient, fixed_test_org, cap: object
) -> None:
    pod_id, schedule_id = await _capped_agent_schedule(
        authenticated_client, fixed_test_org["id"], cap=2
    )
    agent_name = (
        await authenticated_client.get(f"/pods/{pod_id}/schedules/{schedule_id}")
    ).json()["agent_name"]

    created = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "TIME",
            "agent_name": agent_name,
            "name": f"refused-{uuid4().hex[:6]}",
            "config": {"cron": "0 0 1 1 *", "max_concurrent_runs": cap},
        },
    )
    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule_id}",
        json={"config": {"cron": "0 0 1 1 *", "max_concurrent_runs": cap}},
    )

    assert created.status_code == 422, created.text
    assert updated.status_code == 422, updated.text
    assert "max_concurrent_runs" in created.text


async def test_the_cap_can_be_changed_and_removed(
    authenticated_client: AsyncClient, fixed_test_org
) -> None:
    pod_id, schedule_id = await _capped_agent_schedule(
        authenticated_client, fixed_test_org["id"], cap=2
    )

    raised = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule_id}",
        json={"config": {"cron": "0 0 1 1 *", "max_concurrent_runs": 5}},
    )
    assert raised.status_code == 200, raised.text
    assert raised.json()["config"]["max_concurrent_runs"] == 5

    removed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule_id}",
        json={"config": {"cron": "0 0 1 1 *"}},
    )
    assert removed.status_code == 200, removed.text
    assert "max_concurrent_runs" not in removed.json()["config"]

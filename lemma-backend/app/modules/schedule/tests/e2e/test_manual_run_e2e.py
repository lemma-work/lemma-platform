"""Running a schedule now, by hand, against real Postgres and the work runtime.

The run takes a timer's path: reserved in the request with its
``schedule.start_target`` item, started by that job, held to the same daily
ceiling and ``max_concurrent_runs``. It runs as the person who asked, who must
be able to change the schedule and run its target, and it is refused for a
schedule with no event to give it or one that is paused.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.work import WorkClass
from app.modules.schedule.tests.e2e.test_trigger_work_e2e import _webhook_schedule
from app.modules.schedule.tests.e2e.trigger_work_support import (
    EVERY_HALF_HOUR,
    create_pod,
    create_time_schedule,
    daily_fire_cap,
    items,
    run,
    runs,
    scalar,
    schedule_row,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


def _runs_path(pod_id: UUID | str, schedule_id: UUID | str) -> str:
    return f"/pods/{pod_id}/schedules/{schedule_id}/runs"


async def _member(
    admin: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: UUID,
    role: str,
) -> dict[str, str]:
    user = await signup_user(async_client, f"manual-run-{role.lower()}")
    org_member = await invite_org_member(admin, async_client, org_id=org_id, user=user)
    await add_pod_member(
        admin,
        pod_id=str(pod_id),
        organization_member_id=org_member["id"],
        role=role,
    )
    return user


async def _pod_wide_schedule(
    client: AsyncClient, pod_id: UUID, *, agent_name: str = "POD_DEFAULT"
) -> dict[str, object]:
    """A TIME schedule every member can see, so the refusals are about acting."""
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "TIME",
            "agent_name": agent_name,
            "instruction": "Say hello.",
            "visibility": "POD",
            "config": {"cron": EVERY_HALF_HOUR},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_running_a_time_schedule_now_starts_its_target_once_through_the_runtime(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)
    cursor = (await schedule_row(schedule["id"]))["next_fire_at"]

    response = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))

    assert response.status_code == 202, response.text
    reserved = response.json()
    assert reserved["source_event_id"].startswith("manual:")
    assert (reserved["status"], reserved["attempts"]) == ("PROCESSING", 0)
    assert reserved["user_id"] == fixed_test_user["id"]
    assert reserved["metadata"] == {
        "manual": True,
        "started_by_user_id": fixed_test_user["id"],
    }
    [start] = await items("schedule.start_target")
    assert start.payload["schedule_run_id"] == reserved["id"]
    assert start.rate_key == f"schedule:{schedule['id']}"
    # Outside its timing: the timer's own next occurrence has not moved.
    assert (await schedule_row(schedule["id"]))["next_fire_at"] == cursor
    [fire] = await items("schedule.fire", state="queued")
    assert fire.run_at == cursor

    await run(WorkClass.AUTOMATION)

    [started] = await runs(schedule["id"])
    assert (started["status"], started["attempts"]) == ("DISPATCHED", 1)
    conversation = await scalar(
        "SELECT count(*) FROM agent_conversations WHERE id = CAST(:id AS uuid)",
        id=started["target_run_id"],
    )
    assert conversation == 1
    listed = await authenticated_client.get(_runs_path(pod_id, schedule["id"]))
    assert [item["id"] for item in listed.json()["items"]] == [reserved["id"]]


async def test_each_run_by_hand_is_a_run_of_its_own(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)

    first = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))
    second = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))

    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json()["id"] != second.json()["id"]
    assert len(await items("schedule.start_target")) == 2


async def test_a_webhook_schedule_cannot_be_run_now_having_no_event_to_carry(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _webhook_schedule(pod_id, UUID(fixed_test_user["id"]))

    response = await authenticated_client.post(_runs_path(pod_id, schedule.id))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "SCHEDULE_RUN_NEEDS_EVENT"
    assert await runs(schedule.id) == []
    assert await items("schedule.start_target") == []


async def test_a_paused_schedule_is_refused_until_it_is_resumed(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)
    schedule_path = f"/pods/{pod_id}/schedules/{schedule['id']}"
    paused = await authenticated_client.patch(schedule_path, json={"is_active": False})
    assert paused.status_code == 200, paused.text

    refused = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "SCHEDULE_INACTIVE"
    assert await runs(schedule["id"]) == []

    resumed = await authenticated_client.patch(schedule_path, json={"is_active": True})
    assert resumed.status_code == 200, resumed.text
    accepted = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))
    assert accepted.status_code == 202, accepted.text


async def test_a_full_schedule_skips_a_run_by_hand_and_records_the_skip(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(
        authenticated_client, pod_id, max_concurrent_runs=1
    )
    first = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))
    assert first.status_code == 202, first.text

    refused = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "SCHEDULE_AT_CAPACITY"
    in_flight, skipped = await runs(schedule["id"])
    assert (in_flight["id"], in_flight["status"]) == (
        UUID(first.json()["id"]),
        "PROCESSING",
    )
    # Recorded where the owner looks, as a timer's skipped fire is.
    assert skipped["status"] == "FILTERED"
    assert skipped["metadata"]["skipped_because"] == "max_concurrent_runs"
    assert len(await items("schedule.start_target")) == 1


async def test_a_run_by_hand_past_the_daily_ceiling_pauses_the_schedule_like_any_fire(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await create_time_schedule(authenticated_client, pod_id)
    async with daily_fire_cap(pod_id, 1):
        first = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))
        capped = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))

    assert first.status_code == 202, first.text
    assert capped.status_code == 409, capped.text
    assert capped.json()["code"] == "SCHEDULE_FIRE_CAP_REACHED"
    row = await schedule_row(schedule["id"])
    assert (row["is_active"], row["paused_reason"]) == (False, "fire_cap")
    [notice] = await items("schedule.paused_notice")
    assert notice.payload["reason"] == "fire_cap"
    skips = await scalar(
        "SELECT count(*) FROM trigger_skips WHERE schedule_id = :id "
        "AND reason = 'fire_cap'",
        id=schedule["id"],
    )
    assert skips == 1
    assert [r["status"] for r in await runs(schedule["id"])] == ["PROCESSING"]
    after = await authenticated_client.post(_runs_path(pod_id, schedule["id"]))
    assert after.status_code == 409, after.text
    assert after.json()["code"] == "SCHEDULE_INACTIVE"


async def test_an_editor_runs_another_persons_schedule_as_themselves(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _pod_wide_schedule(authenticated_client, pod_id)
    editor = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_EDITOR",
    )

    response = await async_client.post(
        _runs_path(pod_id, schedule["id"]), headers=auth_headers(editor)
    )

    assert response.status_code == 202, response.text
    assert response.json()["user_id"] == editor["id"]
    assert response.json()["metadata"]["started_by_user_id"] == editor["id"]
    # Running it changes nothing about whose schedule it is.
    detail = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}"
    )
    assert detail.json()["user_id"] == fixed_test_user["id"]


async def test_a_member_who_may_not_change_the_schedule_cannot_run_it_now(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _pod_wide_schedule(authenticated_client, pod_id)
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
    )

    response = await async_client.post(
        _runs_path(pod_id, schedule["id"]), headers=auth_headers(user)
    )

    assert response.status_code == 403, response.text
    assert await runs(schedule["id"]) == []


async def test_an_editor_who_may_not_run_the_agent_cannot_run_its_schedule_now(
    authenticated_client: AsyncClient, async_client: AsyncClient, fixed_test_org
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    restricted = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"restricted_{uuid4().hex[:6]}",
            "instruction": "Acknowledge briefly.",
            "visibility": "RESTRICTED",
        },
    )
    assert restricted.status_code == 201, restricted.text
    schedule = await _pod_wide_schedule(
        authenticated_client, pod_id, agent_name=restricted.json()["name"]
    )
    editor = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_EDITOR",
    )

    response = await async_client.post(
        _runs_path(pod_id, schedule["id"]), headers=auth_headers(editor)
    )

    assert response.status_code == 403, response.text
    assert await runs(schedule["id"]) == []

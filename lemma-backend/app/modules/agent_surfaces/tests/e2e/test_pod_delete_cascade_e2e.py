"""End-to-end: deleting a pod cascades cleanup of its schedules and surfaces.

This drives the full event path through the real streaq worker: the in-process
app soft-deletes the pod and publishes ``PodDeletedEvent`` to Redis; the worker
subprocess consumes it on the ``pod_events`` stream and runs the per-module
cleanup handlers (schedule + agent_surfaces). We then poll the DB until every
schedule and surface for the pod is gone, and confirm the freed account can bind
to a brand-new surface in another pod.

Schedules are seeded directly through the repository (DATASTORE type, including
one ``is_internal`` row) rather than the API: it avoids a dependency on the
external scheduler service and proves the cleanup deletes internal schedules too
— the worker still tears them down via the real ``ScheduleService`` path.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import _ensure_connector_account
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository

pytestmark = pytest.mark.e2e

CLEANUP_TIMEOUT_SECONDS = 60
CLEANUP_POLL_SECONDS = 0.5


async def _create_pod(client: AsyncClient, org_id: str, *, name: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": name,
            "description": "Pod-delete cascade e2e",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _seed_datastore_schedule(
    db_session,
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    is_internal: bool = False,
) -> None:
    repo = ScheduleRepository(session=db_session)
    await repo.create(
        ScheduleEntity(
            user_id=user_id,
            pod_id=pod_id,
            name=name,
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "cascade_records", "operations": ["INSERT"]},
            is_internal=is_internal,
        )
    )
    await db_session.commit()


async def _count_pod_children(db_manager, pod_uuid: UUID) -> tuple[int, int]:
    """Count schedules + surfaces for a pod via a fresh session.

    A fresh session each poll is required so we observe the worker subprocess'
    committed deletes rather than a stale snapshot from a long-lived session.
    """
    async with db_manager.session_factory() as session:
        schedules = await session.scalar(
            select(func.count())
            .select_from(Schedule)
            .where(Schedule.pod_id == pod_uuid)
        )
        surfaces = await session.scalar(
            select(func.count())
            .select_from(AgentSurface)
            .where(AgentSurface.pod_id == pod_uuid)
        )
    return int(schedules or 0), int(surfaces or 0)


@pytest.mark.asyncio
async def test_pod_delete_cascades_schedule_and_surface_cleanup(
    authenticated_client: AsyncClient,
    db_session,
    db_manager,
    fixed_test_user,
    fixed_test_org,
    fake_slack,
    worker,
    monkeypatch,
):
    _ = worker  # real streaq worker subprocess consuming pod_events
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")

    org_id = fixed_test_org["id"]
    user_id = UUID(fixed_test_user["id"])
    pod_id = await _create_pod(
        authenticated_client, org_id, name=f"Cascade Pod {uuid4().hex[:6]}"
    )
    pod_uuid = UUID(pod_id)

    # Two schedules: a normal one and an internal one (cleanup must delete both).
    await _seed_datastore_schedule(
        db_session, pod_id=pod_uuid, user_id=user_id, name="cleanup_sched_a"
    )
    await _seed_datastore_schedule(
        db_session,
        pod_id=pod_uuid,
        user_id=user_id,
        name="cleanup_sched_internal",
        is_internal=True,
    )

    # Two surfaces: a system WhatsApp surface (no account) and an account-bound
    # Slack surface (so we can prove the account is released on cleanup).
    whatsapp = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces", json={"platform": "WHATSAPP"}
    )
    assert whatsapp.status_code == 200, whatsapp.text

    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-cascade",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    slack = await authenticated_client.post(
        f"/pods/{pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert slack.status_code == 200, slack.text

    # Precondition: everything is persisted under the pod.
    schedules, surfaces = await _count_pod_children(db_manager, pod_uuid)
    assert schedules == 2, f"expected 2 schedules, got {schedules}"
    assert surfaces == 2, f"expected 2 surfaces, got {surfaces}"

    # Delete the pod -> emits PodDeletedEvent -> worker cleans up asynchronously.
    deleted = await authenticated_client.delete(f"/pods/{pod_id}")
    assert deleted.status_code == 204, deleted.text

    # Poll until the worker has removed every schedule and surface for the pod.
    deadline = asyncio.get_running_loop().time() + CLEANUP_TIMEOUT_SECONDS
    schedules = surfaces = -1
    while asyncio.get_running_loop().time() < deadline:
        schedules, surfaces = await _count_pod_children(db_manager, pod_uuid)
        if schedules == 0 and surfaces == 0:
            break
        await asyncio.sleep(CLEANUP_POLL_SECONDS)

    assert schedules == 0, f"schedules not cleaned up after pod delete: {schedules}"
    assert surfaces == 0, f"surfaces not cleaned up after pod delete: {surfaces}"

    # The Slack account is freed: it binds to a fresh surface in another pod
    # (this would 400 with an org-uniqueness conflict if cleanup had not run).
    sibling_pod_id = await _create_pod(
        authenticated_client, org_id, name=f"Sibling Pod {uuid4().hex[:6]}"
    )
    reused = await authenticated_client.post(
        f"/pods/{sibling_pod_id}/surfaces",
        json={"platform": "SLACK", "account_id": str(account.id)},
    )
    assert reused.status_code == 200, reused.text

"""A pod's lifecycle writes its work in the same transaction, and nothing else.

Real Postgres, the real API, and the work runtime driven by ``run_until_idle``:

* creating a pod writes ``pod.provision`` and running it creates the schema;
* deleting a pod drops the pod's queued work, leaves running and other pods'
  work alone, and writes one cleanup item per ``PodDeleted`` subscriber under
  the organization -- which survive the drop and run;
* a join request mails each admin once, however often its work is run.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    WorkClass,
    WorkWriter,
    org_namespace,
    pod_namespace,
)
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.datastore.events.pod_provision_work import POD_PROVISION
from app.modules.datastore.services.wiring import get_schema_manager
from app.modules.pod.events.join_request_work import (
    POD_JOIN_REQUEST_EMAIL,
    JoinRequestEmailPayload,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.test_support.e2e_authz import signup_user

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]

_CLEANUP_KINDS = {
    "schedule.pod_deleted",
    "agent.pod_links.pod_deleted",
    "surfaces.pod_deleted",
    "mcp_access.pod_deleted",
}


async def _create_pod(client: AsyncClient, org_id: str) -> UUID:
    response = await client.post(
        "/pods",
        json={
            "name": f"Lifecycle Work {uuid4().hex[:6]}",
            "organization_id": org_id,
            "description": "pod lifecycle work e2e",
        },
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


async def _items(ns: str, *, state: str | None = None) -> list[tuple[str, str]]:
    query = "SELECT kind, state FROM work_items WHERE ns = :ns"
    params: dict[str, str] = {"ns": ns}
    if state is not None:
        query += " AND state = :state"
        params["state"] = state
    async with async_session_maker() as session:
        rows = (await session.execute(text(query), params)).all()
    return [(row.kind, row.state) for row in rows]


async def _queue_placeholder(pod_id: UUID, *, running: bool = False) -> UUID:
    """A delivery item in ``pod_id``'s namespace that nothing here will run."""
    async with async_session_maker() as session:
        item_id = await WorkWriter(session).add(
            POD_JOIN_REQUEST_EMAIL,
            pod_namespace(pod_id),
            JoinRequestEmailPayload(
                join_request_id=uuid4(),
                pod_id=pod_id,
                organization_id=uuid4(),
                requester_user_id=uuid4(),
            ),
            delay=timedelta(hours=1),
        )
        assert item_id is not None
        if running:
            await session.execute(
                text(
                    "UPDATE work_items SET state = 'running', lease_owner = 'elsewhere', "
                    "lease_until = now() + interval '1 hour' WHERE id = :id"
                ),
                {"id": item_id},
            )
        await session.commit()
    return item_id


async def _forget(item_ids: list[UUID]) -> None:
    async with async_session_maker() as session:
        await session.execute(
            text("DELETE FROM work_items WHERE id = ANY(:ids)"), {"ids": item_ids}
        )
        await session.commit()


async def test_creating_a_pod_writes_its_provisioning_and_running_it_makes_the_schema(
    authenticated_client: AsyncClient, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])

    async with async_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT ns, class, dedup_key FROM work_items "
                    "WHERE kind = :kind AND payload->>'pod_id' = :pod_id"
                ),
                {"kind": POD_PROVISION, "pod_id": str(pod_id)},
            )
        ).one()
    assert (row.ns, row[1], row.dedup_key) == (
        pod_namespace(pod_id),
        WorkClass.EVENTS.value,
        f"pod-provision:{pod_id}",
    )

    await get_schema_manager().drop_datastore_schema(pod_id)
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    assert await get_schema_manager().datastore_schema_exists(pod_id)
    assert (POD_PROVISION, "queued") not in await _items(pod_namespace(pod_id))


async def test_deleting_a_pod_drops_its_queued_work_but_its_cleanup_runs(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user, db_session
):
    org_id = UUID(fixed_test_org["id"])
    pod_id = await _create_pod(authenticated_client, str(org_id))
    bystander_id = await _create_pod(authenticated_client, str(org_id))
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    await ScheduleRepository(SqlAlchemyUnitOfWork(db_session)).create(
        ScheduleEntity(
            user_id=UUID(fixed_test_user["id"]),
            pod_id=pod_id,
            name="goes_with_the_pod",
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "lifecycle_rows", "operations": ["INSERT"]},
        )
    )
    await db_session.commit()
    queued = await _queue_placeholder(pod_id)
    running = await _queue_placeholder(pod_id, running=True)
    elsewhere = await _queue_placeholder(bystander_id)
    try:
        deleted = await authenticated_client.delete(f"/pods/{pod_id}")
        assert deleted.status_code == 204, deleted.text

        # The pod's waiting work went with it; what was already running is
        # left to finish or lose its lease, and another pod's is untouched.
        assert await _items(pod_namespace(pod_id), state="queued") == []
        assert await _items(pod_namespace(pod_id), state="running") == [
            (POD_JOIN_REQUEST_EMAIL, "running")
        ]
        assert await _items(pod_namespace(bystander_id), state="queued") == [
            (POD_JOIN_REQUEST_EMAIL, "queued")
        ]
        # Every subscriber's cleanup is queued under the organization.
        cleanup = {kind for kind, _ in await _items(org_namespace(org_id))}
        assert _CLEANUP_KINDS <= cleanup

        await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

        assert not _CLEANUP_KINDS & {
            kind for kind, _ in await _items(org_namespace(org_id))
        }
        async with async_session_maker() as session:
            left = await session.scalar(
                select(func.count())
                .select_from(Schedule)
                .where(Schedule.pod_id == pod_id)
            )
        assert left == 0

        # Deleting again is a no-op: nothing new is written.
        again = await authenticated_client.delete(f"/pods/{pod_id}")
        assert again.status_code == 204, again.text
        assert not _CLEANUP_KINDS & {
            kind for kind, _ in await _items(org_namespace(org_id))
        }
    finally:
        await _forget([queued, running, elsewhere])


def _emails_to(output_dir: str, recipient: str, subject_part: str) -> int:
    count = 0
    for path in Path(output_dir).glob("*.json"):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if message.get("to_email") == recipient and subject_part in str(
            message.get("subject", "")
        ):
            count += 1
    return count


async def test_a_join_request_mails_each_admin_exactly_once(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    fixed_test_user,
    e2e_settings,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    outsider = await signup_user(async_client, f"pod-join-work-{uuid4().hex[:10]}")

    requested = await async_client.post(
        f"/pods/{pod_id}/join-requests",
        headers={"Authorization": f"Bearer {outsider['token']}"},
    )
    assert requested.status_code == 201, requested.text
    assert (POD_JOIN_REQUEST_EMAIL, "queued") in await _items(pod_namespace(pod_id))

    admin_email = fixed_test_user["email"]
    before = _emails_to(e2e_settings.email_output_dir, admin_email, "join")
    await run_until_idle(async_session_maker, classes=[WorkClass.DELIVERY])
    await run_until_idle(async_session_maker, classes=[WorkClass.DELIVERY])

    assert _emails_to(e2e_settings.email_output_dir, admin_email, "join") == before + 1
    assert await _items(pod_namespace(pod_id)) == []

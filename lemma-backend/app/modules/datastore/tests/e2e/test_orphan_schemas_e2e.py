"""A deleted pod's schema is dropped after the retention window, and nothing else.

Deleting a pod is a soft delete in the platform database, and its schema in the
datastore database used to stay forever. The nightly cron drops it once the pod
has been gone for ``DATASTORE_ORPHAN_SCHEMA_RETENTION_DAYS``. Getting this wrong
in one direction is a storage leak; in the other it destroys a live pod's data,
so the cases that must survive are asserted as carefully as the one that must
not.

Real databases on both sides, because the pod module's answer is the only thing
standing between a schema and ``DROP SCHEMA ... CASCADE``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.events.orphan_schema_tasks import reap_orphan_pod_schemas
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.tests.e2e.harness import pod_payload

pytestmark = pytest.mark.e2e


async def _make_pod(client: AsyncClient, organization_id: str) -> UUID:
    response = await client.post("/pods", json=pod_payload(organization_id))
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return UUID(response.json()["id"])


async def _delete_pod(client: AsyncClient, pod_id: UUID) -> None:
    response = await client.delete(f"/pods/{pod_id}")
    assert response.status_code < 300, response.text


async def _schema_exists(manager: SchemaManager, pod_id: UUID) -> bool:
    return await manager.datastore_schema_exists(pod_id)


async def test_only_pods_gone_past_retention_lose_their_schema(
    authenticated_client: AsyncClient, fixed_test_org, db_session
):
    manager = SchemaManager()
    org = fixed_test_org["id"]
    live = await _make_pod(authenticated_client, org)
    recently_deleted = await _make_pod(authenticated_client, org)
    long_deleted = await _make_pod(authenticated_client, org)
    never_existed = uuid4()
    for pod_id in (live, recently_deleted, long_deleted, never_existed):
        await manager.create_datastore_schema(pod_id)
    await _delete_pod(authenticated_client, recently_deleted)
    await _delete_pod(authenticated_client, long_deleted)

    # Deletion must stamp the row: it is the only record of when it happened.
    deleted_at = await db_session.scalar(
        text("SELECT updated_at FROM pods WHERE id = :id"), {"id": recently_deleted}
    )
    assert datetime.now(timezone.utc) - deleted_at < timedelta(minutes=5)

    retention = timedelta(
        days=datastore_settings.datastore_orphan_schema_retention_days
    )
    await db_session.execute(
        text("UPDATE pods SET updated_at = :at WHERE id = :id"),
        {
            "at": datetime.now(timezone.utc) - retention - timedelta(days=1),
            "id": long_deleted,
        },
    )
    await db_session.commit()

    summary = await reap_orphan_pod_schemas()

    assert summary is not None and summary.dropped >= 2
    assert await _schema_exists(manager, live), "a live pod's schema was dropped"
    assert await _schema_exists(manager, recently_deleted), (
        "a pod deleted inside the retention window lost its schema"
    )
    assert not await _schema_exists(manager, long_deleted)
    assert not await _schema_exists(manager, never_existed)


async def test_a_schema_that_is_not_a_pods_is_left_alone(db_manager):
    manager = SchemaManager()
    async with manager.session_factory() as session:
        await session.execute(text('CREATE SCHEMA IF NOT EXISTS "pod_scratch"'))
        await session.commit()
    try:
        await reap_orphan_pod_schemas()

        async with manager.session_factory() as session:
            kept = await session.scalar(
                text("SELECT 1 FROM pg_namespace WHERE nspname = 'pod_scratch'")
            )
        assert kept == 1
    finally:
        async with manager.session_factory() as session:
            await session.execute(text('DROP SCHEMA IF EXISTS "pod_scratch"'))
            await session.commit()


async def test_retention_zero_switches_the_cleanup_off(db_manager, monkeypatch):
    manager = SchemaManager()
    orphan = uuid4()
    await manager.create_datastore_schema(orphan)
    monkeypatch.setattr(datastore_settings, "datastore_orphan_schema_retention_days", 0)

    assert await reap_orphan_pod_schemas() is None
    assert await _schema_exists(manager, orphan)
    await manager.drop_datastore_schema(orphan)

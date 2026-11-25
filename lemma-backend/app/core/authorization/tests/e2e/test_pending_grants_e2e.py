"""Settling a key drains every waiting row, however many batches it takes.

The batch is shrunk to two rows so five rows take three reads, and one
grantee's three permissions span the first two. Every grant must land on the
one resource, with each grantee's whole set, and nothing may be left waiting.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.authorization import pending_grants
from app.core.authorization.context import ResourceType
from app.core.authorization.models import (
    PendingResourceGrantModel,
    ResourcePermissionGrantModel,
)
from app.core.authorization.pending_grants import (
    PendingGrant,
    replace_grantee_pending_grants,
    settle_pending_grants,
)

pytestmark = pytest.mark.e2e

_KEY = "batch_test:PER_MEMBER"


async def _hold(db_session, pod_id: UUID, grantee_id: UUID, *permissions: str) -> None:
    await replace_grantee_pending_grants(
        db_session,
        ctx=SimpleNamespace(  # type: ignore[arg-type]
            is_superuser=True, is_named_workload=False, permission_ids=frozenset()
        ),
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=grantee_id,
        resource_type=ResourceType.CONNECTOR,
        grants=[PendingGrant(ResourceType.CONNECTOR, _KEY, permissions)],
        created_by_user_id=None,
    )


async def test_every_batch_settles_onto_the_one_resource(
    scenario, db_session, monkeypatch
):
    await scenario.create_org_with_pod(name_prefix="Batches")
    pod_id = UUID(scenario.pod_id)
    monkeypatch.setattr(pending_grants, "_SETTLE_BATCH", 2)
    spans, second, third = uuid4(), uuid4(), uuid4()
    await _hold(
        db_session,
        pod_id,
        spans,
        "connector.use",
        "datastore.table.read",
        "datastore.record.read",
    )
    await _hold(db_session, pod_id, second, "connector.use")
    await _hold(db_session, pod_id, third, "datastore.table.read")
    await db_session.commit()

    resource_id = uuid4()
    settled = await settle_pending_grants(
        db_session,
        pod_id=pod_id,
        resource_type=ResourceType.CONNECTOR,
        resource_key=_KEY,
        resource_id=resource_id,
    )
    await db_session.commit()

    assert settled == 3
    rows = await db_session.execute(
        select(
            ResourcePermissionGrantModel.grantee_id,
            ResourcePermissionGrantModel.permission_id,
        ).where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_id == resource_id,
        )
    )
    granted: dict[UUID, set[str]] = {}
    for grantee_id, permission_id in rows.all():
        granted.setdefault(grantee_id, set()).add(permission_id)
    assert granted == {
        spans: {"connector.use", "datastore.record.read", "datastore.table.read"},
        second: {"connector.use"},
        third: {"datastore.table.read"},
    }
    left = await db_session.scalar(
        select(PendingResourceGrantModel.id).where(
            PendingResourceGrantModel.pod_id == pod_id
        )
    )
    assert left is None


async def test_replacing_locks_every_key_the_grantee_waits_for(
    scenario, db_session, monkeypatch
):
    """Five keys read two at a time: the lock taken before a replacement must
    cover all five, not the first page, or a settlement of a key past it could
    grant from a row the replacement deletes."""
    await scenario.create_org_with_pod(name_prefix="Keys")
    pod_id = UUID(scenario.pod_id)
    monkeypatch.setattr(pending_grants, "_SETTLE_BATCH", 2)
    grantee = uuid4()
    await replace_grantee_pending_grants(
        db_session,
        ctx=SimpleNamespace(  # type: ignore[arg-type]
            is_superuser=True, is_named_workload=False, permission_ids=frozenset()
        ),
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=grantee,
        resource_type=ResourceType.CONNECTOR,
        grants=[
            PendingGrant(
                ResourceType.CONNECTOR, f"key-{n}:PER_MEMBER", ("connector.use",)
            )
            for n in range(5)
        ],
        created_by_user_id=None,
    )
    await db_session.commit()

    await pending_grants.lock_grantee_pending_keys(
        db_session,
        pod_id=pod_id,
        resource_type=ResourceType.CONNECTOR,
        grantee_type="AGENT",
        grantee_id=grantee,
    )
    held = await db_session.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND pid = pg_backend_pid() AND granted"
        )
    )
    await db_session.rollback()

    assert held == 5


async def test_a_replace_keeps_what_another_resource_type_waits_for(
    scenario, db_session
):
    """A module replaces only what it holds: clearing a grantee's connector
    waits leaves one another module holds for a table."""
    await scenario.create_org_with_pod(name_prefix="Scoped")
    pod_id = UUID(scenario.pod_id)
    grantee = uuid4()
    importer = SimpleNamespace(  # type: ignore[arg-type]
        is_superuser=True, is_named_workload=False, permission_ids=frozenset()
    )
    for resource_type, key in (
        (ResourceType.DATASTORE_TABLE, "notes"),
        (ResourceType.CONNECTOR, _KEY),
    ):
        await replace_grantee_pending_grants(
            db_session,
            ctx=importer,
            pod_id=pod_id,
            grantee_type="AGENT",
            grantee_id=grantee,
            resource_type=resource_type,
            grants=[PendingGrant(resource_type, key, ("datastore.table.read",))],
            created_by_user_id=None,
        )
    await replace_grantee_pending_grants(
        db_session,
        ctx=importer,
        pod_id=pod_id,
        grantee_type="AGENT",
        grantee_id=grantee,
        resource_type=ResourceType.CONNECTOR,
        grants=[],
        created_by_user_id=None,
    )
    await db_session.commit()

    left = await db_session.execute(
        select(PendingResourceGrantModel.resource_type).where(
            PendingResourceGrantModel.pod_id == pod_id
        )
    )
    assert left.scalars().all() == [ResourceType.DATASTORE_TABLE.value]


async def test_a_save_locks_what_the_grantee_waits_for_before_deleting(scenario):
    """A declaration's settlement already holds the agent's waiting key when an
    editor saves the agent's permissions. The save must take that key before
    deleting anything: it waits, and its delete then removes the grant the
    settlement wrote. Deleting first would miss it, and the agent would keep a
    grant the editor had just removed."""
    from app.core.authorization.grants import replace_grantee_resource_grants
    from app.core.authorization.pending_grants import lock_pending_key
    from app.core.infrastructure.db.session import async_session_maker
    from app.modules.test_support.e2e.waiters import eventually

    await scenario.create_org_with_pod(name_prefix="Saved")
    pod_id = UUID(scenario.pod_id)
    grantee, resource_id = uuid4(), uuid4()
    async with async_session_maker() as session:
        await _hold(session, pod_id, grantee, "connector.use")
        await session.commit()

    async with async_session_maker() as declaring, async_session_maker() as saving:
        await lock_pending_key(
            declaring,
            pod_id=pod_id,
            resource_type=ResourceType.CONNECTOR,
            resource_key=_KEY,
        )
        holder = await declaring.scalar(text("SELECT pg_backend_pid()"))
        waiter = await saving.scalar(text("SELECT pg_backend_pid()"))
        save = asyncio.create_task(
            replace_grantee_resource_grants(
                saving,
                ctx=SimpleNamespace(  # type: ignore[arg-type]
                    is_superuser=True,
                    is_named_workload=False,
                    permission_ids=frozenset(),
                ),
                pod_id=pod_id,
                grantee_type="AGENT",
                grantee_id=grantee,
                grants=[],
                created_by_user_id=None,
            )
        )

        async def blocked() -> bool:
            async with async_session_maker() as probe:
                return bool(
                    await probe.scalar(
                        text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                        {"holder": holder, "waiter": waiter},
                    )
                )

        settled = False
        try:
            await eventually(
                label="the save waits for the settlement's key",
                probe=blocked,
                done=bool,
                timeout_seconds=10,
                fail_fast=lambda _b: "the save never waited" if save.done() else None,
            )
            await settle_pending_grants(
                declaring,
                pod_id=pod_id,
                resource_type=ResourceType.CONNECTOR,
                resource_key=_KEY,
                resource_id=resource_id,
            )
            settled = True
        finally:
            await declaring.commit()
            if not settled:
                save.cancel()
            await asyncio.gather(save, return_exceptions=True)
            await saving.commit()

    assert await _grants_held_by(pod_id, grantee) == 0


async def _grants_held_by(pod_id: UUID, grantee: UUID) -> int:
    from app.core.infrastructure.db.session import async_session_maker

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).where(
                ResourcePermissionGrantModel.pod_id == pod_id,
                ResourcePermissionGrantModel.grantee_id == grantee,
            )
        )
    return int(count or 0)

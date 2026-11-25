"""Settling a key drains every waiting row, however many batches it takes.

The batch is shrunk to two rows so five rows take three reads, and one
grantee's three permissions span the first two. Every grant must land on the
one resource, with each grantee's whole set, and nothing may be left waiting.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

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

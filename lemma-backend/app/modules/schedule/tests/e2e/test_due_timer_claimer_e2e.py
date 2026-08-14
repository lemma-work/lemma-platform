"""One-shot timers: claimed once, retried if the fire is lost.

A schedule's claim is its cursor moving -- durable, and the next occurrence is a
different row-state. A timer has no next occurrence, so the claim has to be a
lease, and a lease has two properties worth testing separately: it excludes
other replicas while it is live, and it *stops* excluding them when it expires.

The second is the one that matters for correctness. A timer that is claimed and
then never dispatched -- the replica died, the broker was down -- must come back,
because a lost `WAIT_UNTIL` is a workflow that waits forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workflow.services.due_wait_claimer import claim_due_workflow_waits
from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunModel,
    WorkflowRunWaitModel,
)

pytestmark = [pytest.mark.e2e]


@pytest.fixture
async def workflow_run(authenticated_client, fixed_test_org, fixed_test_user, db_session):
    """A real pod, flow and run.

    The wait row carries foreign keys to all three, so inventing UUIDs here
    fails on insert rather than testing anything.
    """
    from uuid import UUID

    from fastapi import status

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"timer-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod_id = UUID(response.json()["id"])
    user_id = UUID(fixed_test_user["id"])

    flow = WorkflowModel(id=uuid4(), pod_id=pod_id, name=f"flow-{uuid4().hex[:6]}")
    db_session.add(flow)
    await db_session.flush()
    run = WorkflowRunModel(
        id=uuid4(), flow_id=flow.id, pod_id=pod_id, user_id=user_id
    )
    db_session.add(run)
    await db_session.commit()
    return run.id, flow.id, pod_id


async def _insert_wait(session, workflow_run, *, due_at, lease_until=None):
    run_id, flow_id, pod_id = workflow_run
    wait = WorkflowRunWaitModel(
        id=uuid4(),
        run_id=run_id,
        flow_id=flow_id,
        pod_id=pod_id,
        node_id="wait-node",
        wait_type="TIME",
        status="ACTIVE",
        external_ref=str(uuid4()),
        scheduled_at=due_at,
        fire_lease_until=lease_until,
        payload={"scheduled_at": due_at.isoformat()},
    )
    session.add(wait)
    await session.commit()
    return wait.id, wait.external_ref


async def test_a_due_timer_is_claimed_and_leased(db_manager, db_session, workflow_run) -> None:
    now = datetime.now(timezone.utc)
    wait_id, external_ref = await _insert_wait(
        db_session, workflow_run, due_at=now - timedelta(seconds=5)
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_workflow_waits(session, now=now)
        await session.commit()

    mine = [c for c in claimed if str(c.timer_id) == external_ref]
    assert len(mine) == 1
    assert mine[0].payload["source"] == "workflow_wait_until"
    assert mine[0].payload["wait_ref"] == external_ref

    await db_session.rollback()
    row = await db_session.get(WorkflowRunWaitModel, wait_id)
    await db_session.refresh(row)
    assert row.fire_lease_until is not None and row.fire_lease_until > now


async def test_a_live_lease_hides_the_timer_from_other_replicas(
    db_manager, db_session, workflow_run
) -> None:
    """Without this, every replica dispatches the same wake on every tick."""
    now = datetime.now(timezone.utc)
    _, external_ref = await _insert_wait(
        db_session,
        workflow_run,
        due_at=now - timedelta(seconds=5),
        lease_until=now + timedelta(seconds=30),
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_workflow_waits(session, now=now)
        await session.commit()

    assert external_ref not in [str(c.timer_id) for c in claimed]


async def test_an_expired_lease_lets_the_timer_be_retried(
    db_manager, db_session, workflow_run
) -> None:
    """The property a lost fire depends on.

    If the replica holding the lease died before dispatching, nothing else
    recovers this wake -- the workflow simply waits forever. So an expired lease
    must return the timer to the pool rather than leave it claimed.
    """
    now = datetime.now(timezone.utc)
    _, external_ref = await _insert_wait(
        db_session,
        workflow_run,
        due_at=now - timedelta(minutes=10),
        lease_until=now - timedelta(seconds=1),
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_workflow_waits(session, now=now)
        await session.commit()

    assert external_ref in [str(c.timer_id) for c in claimed]


async def test_a_timer_that_is_not_due_is_left_alone(db_manager, db_session, workflow_run) -> None:
    now = datetime.now(timezone.utc)
    _, external_ref = await _insert_wait(
        db_session, workflow_run, due_at=now + timedelta(minutes=5)
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_workflow_waits(session, now=now)
        await session.commit()

    assert external_ref not in [str(c.timer_id) for c in claimed]


async def test_the_fire_time_is_the_due_instant_not_the_poll_time(
    db_manager, db_session, workflow_run
) -> None:
    """Same reason as schedules: the dedup key is built from it."""
    due = datetime.now(timezone.utc) - timedelta(minutes=3)
    _, external_ref = await _insert_wait(db_session, workflow_run, due_at=due)

    async with db_manager.session_factory() as session:
        claimed = await claim_due_workflow_waits(
            session, now=datetime.now(timezone.utc)
        )
        await session.commit()

    mine = [c for c in claimed if str(c.timer_id) == external_ref]
    assert len(mine) == 1
    assert mine[0].fire_at == due
    assert mine[0].payload["scheduled_at"] == due.isoformat()

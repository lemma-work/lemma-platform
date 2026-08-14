"""Two replicas, one fire. Against real Postgres, because that is the claim.

The whole reason scheduling can run on more than one replica is `FOR UPDATE
SKIP LOCKED` plus advancing the cursor in the same transaction. Neither of those
exists in SQLite or in a mock -- row locking is the mechanism, so a test that
does not take real row locks is testing nothing.

Every test here runs two sessions concurrently on purpose. A sequential test
would pass against an implementation with no locking at all.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.services.due_schedule_claimer import claim_due_schedules

pytestmark = [pytest.mark.e2e]


async def _insert_schedule(session, *, cron: str | None, due_at: datetime, pod_id, user_id):
    config = {"cron": cron} if cron else {"scheduled_at": due_at.isoformat()}
    schedule = Schedule(
        id=uuid4(),
        user_id=user_id,
        pod_id=pod_id,
        name=f"claim-{uuid4().hex[:8]}",
        schedule_type=ScheduleType.TIME,
        config=config,
        is_active=True,
        next_fire_at=due_at,
    )
    session.add(schedule)
    await session.commit()
    return schedule.id


@pytest.fixture
async def pod_and_user(authenticated_client, fixed_test_org, fixed_test_user):
    from fastapi import status

    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"claimer-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    from uuid import UUID

    return UUID(response.json()["id"]), UUID(fixed_test_user["id"])


async def test_two_concurrent_claimers_never_take_the_same_occurrence(
    db_manager, db_session, pod_and_user
) -> None:
    """The HA property, stated directly: N replicas, one fire.

    The overlap is forced rather than hoped for. Simply gathering two claimers
    does not race -- the first commits before the second reads, and the test
    then passes against an implementation with no locking at all (verified by
    deleting `FOR UPDATE SKIP LOCKED` and watching it stay green).

    So the second claimer is held until the first is provably holding the row
    lock, and the first is held open until the second has finished. That is the
    only arrangement in which `SKIP LOCKED` is the thing under test.
    """
    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    schedule_id = await _insert_schedule(
        db_session, cron="*/5 * * * *", due_at=now - timedelta(seconds=1),
        pod_id=pod_id, user_id=user_id,
    )

    holds_the_lock = asyncio.Event()
    second_is_done = asyncio.Event()

    async def first_claimer() -> list:
        async with db_manager.session_factory() as session:
            claimed = await claim_due_schedules(session, now=now)
            holds_the_lock.set()
            await asyncio.wait_for(second_is_done.wait(), timeout=20)
            await session.commit()
            return claimed

    async def second_claimer() -> list:
        await asyncio.wait_for(holds_the_lock.wait(), timeout=20)
        async with db_manager.session_factory() as session:
            try:
                # Must not block: SKIP LOCKED walks past a locked row. If this
                # times out, the lock is being taken without SKIP LOCKED, which
                # would serialise every replica behind the slowest one.
                return await asyncio.wait_for(
                    claim_due_schedules(session, now=now), timeout=10
                )
            finally:
                await session.commit()
                second_is_done.set()

    first, second = await asyncio.gather(first_claimer(), second_claimer())

    winners = [c for batch in (first, second) for c in batch if c.schedule_id == schedule_id]
    assert len(winners) == 1, (
        f"{len(winners)} replicas claimed the same occurrence; "
        "FOR UPDATE SKIP LOCKED is not holding"
    )


async def test_the_cursor_advances_so_the_same_occurrence_is_not_reclaimed(
    db_manager, db_session, pod_and_user
) -> None:
    """A second poll after a successful claim must find nothing.

    This is what stops a fast poller from firing the same minute repeatedly --
    the claim is only durable because the cursor moved with it.
    """
    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    schedule_id = await _insert_schedule(
        db_session, cron="*/5 * * * *", due_at=now - timedelta(seconds=1),
        pod_id=pod_id, user_id=user_id,
    )

    async with db_manager.session_factory() as session:
        first = await claim_due_schedules(session, now=now)
        await session.commit()
    async with db_manager.session_factory() as session:
        second = await claim_due_schedules(session, now=now)
        await session.commit()

    assert [c.schedule_id for c in first].count(schedule_id) == 1
    assert schedule_id not in [c.schedule_id for c in second]


async def test_a_one_shot_is_retired_by_the_claim_that_fires_it(
    db_manager, db_session, pod_and_user
) -> None:
    """One occurrence means one, even if two replicas poll simultaneously."""
    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    due = now - timedelta(seconds=1)
    schedule_id = await _insert_schedule(
        db_session, cron=None, due_at=due, pod_id=pod_id, user_id=user_id,
    )

    async def claim_once() -> list:
        async with db_manager.session_factory() as session:
            claimed = await claim_due_schedules(session, now=now)
            await session.commit()
            return claimed

    batches = await asyncio.gather(*(claim_once() for _ in range(3)))
    winners = [c for batch in batches for c in batch if c.schedule_id == schedule_id]
    assert len(winners) == 1
    assert winners[0].is_one_shot is True

    await db_session.rollback()
    row = await db_session.get(Schedule, schedule_id)
    await db_session.refresh(row)
    assert row.is_active is False, "a fired one-shot must not stay claimable"
    assert row.next_fire_at is None


async def test_the_claimed_fire_time_is_the_occurrence_not_the_poll_time(
    db_manager, db_session, pod_and_user
) -> None:
    """The dedup key is built from this, so it has to be the occurrence.

    A schedule claimed late must report the instant it was *due*. Reporting the
    poll time would mint a different `source_event_id` for the same occurrence
    and defeat the ledger's unique constraint -- the thing that makes a
    double-fire collapse into one run.
    """
    pod_id, user_id = pod_and_user
    due = datetime.now(timezone.utc) - timedelta(minutes=7)
    schedule_id = await _insert_schedule(
        db_session, cron="*/5 * * * *", due_at=due, pod_id=pod_id, user_id=user_id,
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_schedules(session, now=datetime.now(timezone.utc))
        await session.commit()

    mine = [c for c in claimed if c.schedule_id == schedule_id]
    assert len(mine) == 1
    assert mine[0].fire_at == due, (
        f"reported {mine[0].fire_at.isoformat()}, expected the due instant "
        f"{due.isoformat()} -- the dedup key would not match a retry"
    )


async def test_a_schedule_that_is_not_due_is_left_alone(
    db_manager, db_session, pod_and_user
) -> None:
    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    schedule_id = await _insert_schedule(
        db_session, cron="*/5 * * * *", due_at=now + timedelta(minutes=10),
        pod_id=pod_id, user_id=user_id,
    )

    async with db_manager.session_factory() as session:
        claimed = await claim_due_schedules(session, now=now)
        await session.commit()

    assert schedule_id not in [c.schedule_id for c in claimed]


async def _insert_without_cursor(session, *, config: dict, pod_id, user_id):
    schedule = Schedule(
        id=uuid4(),
        user_id=user_id,
        pod_id=pod_id,
        name=f"backfill-{uuid4().hex[:8]}",
        schedule_type=ScheduleType.TIME,
        config=config,
        is_active=True,
        next_fire_at=None,
    )
    session.add(schedule)
    await session.commit()
    return schedule.id


async def test_a_schedule_with_no_cursor_gets_one(
    db_manager, db_session, pod_and_user
) -> None:
    """Rows predating the column, and rows the API just wrote, both land here."""
    from app.modules.schedule.services.due_schedule_claimer import (
        backfill_missing_cursors,
    )

    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    schedule_id = await _insert_without_cursor(
        db_session, config={"cron": "*/5 * * * *"}, pod_id=pod_id, user_id=user_id
    )

    async with db_manager.session_factory() as session:
        await backfill_missing_cursors(session, now=now)
        await session.commit()

    await db_session.rollback()
    row = await db_session.get(Schedule, schedule_id)
    await db_session.refresh(row)
    assert row.next_fire_at is not None
    assert row.next_fire_at > now
    assert row.is_active is True


async def test_an_unusable_row_is_retired_rather_than_left_invisible(
    db_manager, db_session, pod_and_user
) -> None:
    """A cursor-less active row is worse than an inactive one.

    It reads as scheduled to anyone looking at the table while being invisible
    to the poller forever, so the failure mode is a schedule that silently never
    runs and nothing to show why.
    """
    from app.modules.schedule.services.due_schedule_claimer import (
        backfill_missing_cursors,
    )

    pod_id, user_id = pod_and_user
    schedule_id = await _insert_without_cursor(
        db_session, config={"cron": "not a cron"}, pod_id=pod_id, user_id=user_id
    )

    async with db_manager.session_factory() as session:
        await backfill_missing_cursors(session, now=datetime.now(timezone.utc))
        await session.commit()

    await db_session.rollback()
    row = await db_session.get(Schedule, schedule_id)
    await db_session.refresh(row)
    assert row.is_active is False
    assert row.next_fire_at is None


async def test_a_one_shot_whose_moment_has_passed_is_not_fired_late(
    db_manager, db_session, pod_and_user
) -> None:
    """Matching the behaviour the old reconcile pass had.

    A one-shot missed while the fleet was down is dropped, not fired on the next
    boot -- "send this at 09:00" is rarely still wanted at 14:00.
    """
    from app.modules.schedule.services.due_schedule_claimer import (
        backfill_missing_cursors,
    )

    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=5)).isoformat()
    schedule_id = await _insert_without_cursor(
        db_session, config={"scheduled_at": past}, pod_id=pod_id, user_id=user_id
    )

    async with db_manager.session_factory() as session:
        await backfill_missing_cursors(session, now=now)
        claimed = await claim_due_schedules(session, now=now)
        await session.commit()

    assert schedule_id not in [c.schedule_id for c in claimed]
    await db_session.rollback()
    row = await db_session.get(Schedule, schedule_id)
    await db_session.refresh(row)
    assert row.is_active is False


async def test_a_deactivated_schedule_is_never_claimed(
    db_manager, db_session, pod_and_user
) -> None:
    """Deactivation stops the firing, with nothing else to keep in step.

    This used to need a second system: the failure-streak deactivation wrote
    `is_active = False`, then an event handler asked the scheduler sidecar to
    remove the job. Two systems, two chances to disagree -- a lost event left a
    job firing against a schedule the database considered dead.

    Now the claim query filters on `is_active`, so the UPDATE that deactivates
    is the thing that stops it, in one transaction. This asserts that: a
    deactivated row that is overdue and still carries a cursor is not claimed.
    """
    pod_id, user_id = pod_and_user
    now = datetime.now(timezone.utc)
    schedule_id = await _insert_schedule(
        db_session, cron="*/5 * * * *", due_at=now - timedelta(minutes=1),
        pod_id=pod_id, user_id=user_id,
    )

    row = await db_session.get(Schedule, schedule_id)
    row.is_active = False
    await db_session.commit()

    async with db_manager.session_factory() as session:
        claimed = await claim_due_schedules(session, now=now)
        await session.commit()

    assert schedule_id not in [c.schedule_id for c in claimed], (
        "an overdue-but-deactivated schedule was claimed; deactivation is no "
        "longer sufficient to stop a schedule firing"
    )

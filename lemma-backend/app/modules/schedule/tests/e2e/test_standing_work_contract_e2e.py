"""E2E: ``count_standing_work`` against the real run ledger and visibility SQL.

The contract is a single aggregate, and every rule it states -- what is due,
what is on time, what is left out, who may see which runs -- lives in its WHERE
clause. So the runs are seeded straight into the ledger, each with the
timestamps that decide whether it counts, and the count is asked for as two
different people.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.schedule.contracts.standing_work import (
    ON_TIME_ALLOWANCE,
    StandingWorkCount,
    count_standing_work,
)
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_pod,
    _create_schedule,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_COMPLETED = ScheduleRunStatus.COMPLETED.value


def _week() -> tuple[datetime, datetime]:
    end = datetime.combine(
        datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc
    )
    return end - timedelta(days=7), end


def _run(schedule_id: UUID, due: datetime, **fields: object) -> ScheduleRun:
    values: dict[str, object] = {
        "schedule_id": schedule_id,
        "source_event_id": f"e2e-{uuid4().hex}",
        "status": ScheduleRunStatus.DISPATCHED.value,
        "attempts": 1,
        "target_kind": "AGENT",
        "payload": {},
        "fire_metadata": {},
        "llm_output": {},
        "source_occurred_at": due,
        "started_at": due + timedelta(minutes=1),
    }
    values.update(fields)
    return ScheduleRun(**values)


async def _count_as(db_manager, *, user_id: UUID, pod_id: UUID) -> StandingWorkCount:
    start, end = _week()
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        return await count_standing_work(
            session=uow.session, pod_id=pod_id, ctx=ctx, start=start, end=end
        )


async def _pod_user(authenticated_client, async_client, org_id: str, pod_id: str):
    member = await signup_user(async_client, "standing-work-member")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=org_id, user=member
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_USER",
    )
    return member


async def test_standing_work_counts_due_runs_that_started_on_time_and_completed(
    authenticated_client, fixed_test_org, db_manager
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 9 * * 1"},
    )
    schedule_id = UUID(schedule["id"])
    owner_id = UUID(schedule["user_id"])
    start, _ = _week()

    def due(day: int) -> datetime:
        return start + timedelta(days=day, hours=9)

    async with db_manager.session_factory() as session, session.begin():
        failed = _run(
            schedule_id, due(3), target_outcome=ScheduleRunStatus.TARGET_FAILED.value
        )
        session.add(failed)
        await session.flush()
        session.add_all(
            [
                # On time: started a minute after it was due, and completed.
                _run(schedule_id, due(0), target_outcome=_COMPLETED),
                # Exactly at the allowance still counts.
                _run(
                    schedule_id,
                    due(1),
                    target_outcome=_COMPLETED,
                    started_at=due(1) + ON_TIME_ALLOWANCE,
                ),
                # Completed, but a minute past the allowance.
                _run(
                    schedule_id,
                    due(2),
                    target_outcome=_COMPLETED,
                    started_at=due(2) + ON_TIME_ALLOWANCE + timedelta(minutes=1),
                ),
                # Still in flight when the week closed: due, not done.
                _run(schedule_id, due(4)),
                # Not counted at all: a filter's no, a redrive, and two runs
                # outside the window on either side.
                _run(schedule_id, due(5), status=ScheduleRunStatus.FILTERED.value),
                _run(
                    schedule_id,
                    due(3),
                    target_outcome=_COMPLETED,
                    redrive_of_run_id=failed.id,
                ),
                _run(schedule_id, due(-1), target_outcome=_COMPLETED),
                _run(schedule_id, due(7), target_outcome=_COMPLETED),
            ]
        )
        internal = Schedule(
            user_id=owner_id,
            pod_id=UUID(pod_id),
            schedule_type=ScheduleType.TIME,
            config={},
            is_internal=True,
            visibility="POD",
        )
        session.add(internal)
        await session.flush()
        session.add(_run(internal.id, due(2), target_outcome=_COMPLETED))

    counted = await _count_as(db_manager, user_id=owner_id, pod_id=UUID(pod_id))

    # Due: days 0, 1, 2, 3 and 4. On time and completed: days 0 and 1.
    assert counted == StandingWorkCount(on_time=2, due=5)


async def test_standing_work_counts_only_schedules_the_reader_may_see(
    authenticated_client, async_client, fixed_test_org, db_manager
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    # Agent schedules default to the creator's own; this one is shared on
    # purpose, so the member reading the count may see it.
    shared = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 9 * * 1"},
        visibility="POD",
    )
    restricted = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 10 * * 1"},
        visibility="RESTRICTED",
    )
    member = await _pod_user(
        authenticated_client, async_client, fixed_test_org["id"], pod_id
    )
    owner_id = UUID(shared["user_id"])
    member_id = UUID(member["id"])
    start, _ = _week()
    due = start + timedelta(days=2, hours=9)

    async with db_manager.session_factory() as session, session.begin():
        row_reactions = Schedule(
            user_id=owner_id,
            pod_id=UUID(pod_id),
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "orders", "operations": ["INSERT"]},
            visibility="POD",
        )
        session.add(row_reactions)
        await session.flush()
        session.add_all(
            [
                _run(UUID(shared["id"]), due, target_outcome=_COMPLETED),
                _run(UUID(restricted["id"]), due, target_outcome=_COMPLETED),
                # A row reaction is its row's person's: each reader counts
                # their own and nobody else's.
                _run(
                    row_reactions.id, due, target_outcome=_COMPLETED, user_id=owner_id
                ),
                _run(
                    row_reactions.id, due, target_outcome=_COMPLETED, user_id=member_id
                ),
            ]
        )

    as_owner = await _count_as(db_manager, user_id=owner_id, pod_id=UUID(pod_id))
    as_member = await _count_as(db_manager, user_id=member_id, pod_id=UUID(pod_id))

    # Shared + restricted (the owner's own) + the owner's row reaction.
    assert as_owner == StandingWorkCount(on_time=3, due=3)
    # Shared + the member's row reaction; the restricted schedule is invisible.
    assert as_member == StandingWorkCount(on_time=2, due=2)

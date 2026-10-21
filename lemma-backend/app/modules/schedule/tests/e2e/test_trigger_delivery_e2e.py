"""The shared DATASTORE trigger machinery, cutover seeding and the per-trigger fire rate.

``deliver_page`` is what the change log's delivery calls for each page of a
trigger's changes; it is exercised here directly with synthetic pages.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass, WorkWriter, pod_namespace
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleType,
    TriggerWriteMode,
)
from app.modules.schedule.domain.value_objects import DatastoreOperation
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.trigger_delivery import (
    ChangePosition,
    TriggerChange,
    WriteIntent,
    deliver_page,
)
from app.modules.schedule.tests.e2e.trigger_work_support import (
    daily_fire_cap,
    create_pod,
    execute,
    items,
    run,
    runs,
    scalar,
    schedule_row,
)
from app.modules.schedule.work.kinds import SCHEDULE_START, StartTarget

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


PAGE = 500


async def _datastore_schedule(
    pod_id: UUID, owner: UUID, **config: object
) -> ScheduleEntity:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        entity = await ScheduleRepository(uow=uow).create(
            ScheduleEntity(
                id=uuid4(),
                user_id=owner,
                pod_id=pod_id,
                agent_id=pod_id,
                schedule_type=ScheduleType.DATASTORE,
                config={"table_name": "orders", "operations": ["INSERT"], **config},
                instruction="Look at the order.",
            )
        )
        await uow.commit()
    return entity


def _changes(
    start: int, count: int, intent: WriteIntent, xid: int = 7
) -> list[TriggerChange]:
    return [
        TriggerChange(
            position=ChangePosition(xid, seq),
            operation=DatastoreOperation.INSERT,
            record_id=f"rec-{seq}",
            intent=intent,
            row={"id": f"rec-{seq}", "total": seq},
            occurred_at=datetime.now(timezone.utc),
        )
        for seq in range(start, start + count)
    ]


async def _deliver(schedule: ScheduleEntity, page: list[TriggerChange], **kwargs):
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        current = await ScheduleRepository(uow=uow).get(schedule.id)
        assert current is not None
        result = await deliver_page(uow, current, page, **kwargs)
        await uow.commit()
    return result


async def test_a_ten_thousand_row_bulk_write_is_twenty_skip_rows_not_ten_thousand(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _datastore_schedule(pod_id, UUID(fixed_test_user["id"]))
    bulk = _changes(1, 10_000, WriteIntent.BULK)

    for offset in range(0, len(bulk), PAGE):
        result = await _deliver(schedule, bulk[offset : offset + PAGE])
        assert (result.fired, result.skipped) == (0, {"bulk_write": PAGE})
    # A page delivered again after a crash rewrites its own row.
    await _deliver(schedule, bulk[:PAGE])

    rows = await scalar(
        "SELECT count(*) FROM trigger_skips WHERE schedule_id = :id",
        id=str(schedule.id),
    )
    total = await scalar(
        "SELECT sum(count) FROM trigger_skips WHERE schedule_id = :id AND reason = 'bulk_write'",
        id=str(schedule.id),
    )
    assert (rows, total) == (20, 10_000)
    assert await runs(schedule.id) == []
    assert await items(SCHEDULE_START) == []


async def test_a_change_the_condition_does_not_hold_for_is_recorded_as_skipped(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    """A condition miss starts nothing, but says so: a skip row, and the
    schedule's last fire reads FILTERED -- not the "never arrived" of no status.
    A page that also fires reads as fired."""
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), when={"total": {"equals": 2}}
    )
    path = f"/pods/{pod_id}/schedules/{schedule.id}"

    result = await _deliver(schedule, _changes(1, 3, WriteIntent.NORMAL))
    assert (result.fired, result.skipped) == (1, {"condition_not_met": 2})
    shown = (await authenticated_client.get(path)).json()
    assert shown["last_fire_status"] == "TRIGGERED", shown

    result = await _deliver(schedule, _changes(4, 2, WriteIntent.NORMAL))
    assert (result.fired, result.skipped) == (0, {"condition_not_met": 2})
    shown = (await authenticated_client.get(path)).json()
    assert shown["last_fire_status"] == "FILTERED", shown
    assert len(await runs(schedule.id)) == 1
    skips = (await authenticated_client.get(f"{path}/skips")).json()["items"]
    assert [(s["reason"], s["count"]) for s in skips] == [
        ("condition_not_met", 2),
        ("condition_not_met", 2),
    ]


async def test_write_modes_each_batch_and_the_pod_default(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    owner = UUID(fixed_test_user["id"])
    each = await _datastore_schedule(pod_id, owner, import_writes="each")
    batch = await _datastore_schedule(pod_id, owner, bulk_writes="batch")
    pod_default = await _datastore_schedule(pod_id, owner)

    imported = _changes(1, 3, WriteIntent.IMPORT) + _changes(4, 2, WriteIntent.NORMAL)
    assert (await _deliver(each, imported)).fired == 5

    bulk = _changes(10, 4, WriteIntent.BULK)
    result = await _deliver(batch, bulk)
    assert (result.batches, result.fired) == (1, 0)
    # Replayed: the planned window is found and fires nothing new.
    await _deliver(batch, bulk)
    [batch_run] = await runs(batch.id)
    assert batch_run["source_event_id"] == f"batch:{batch.id}:7:10"
    assert (
        await scalar(
            "SELECT count(*) FROM trigger_batches WHERE schedule_id = :id",
            id=str(batch.id),
        )
        == 1
    )

    result = await _deliver(
        pod_default,
        _changes(20, 3, WriteIntent.BULK),
        pod_bulk_default=TriggerWriteMode.EACH,
    )
    assert result.fired == 3


async def test_a_page_past_the_daily_ceiling_pauses_and_records_the_rest_as_skipped(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    schedule = await _datastore_schedule(pod_id, UUID(fixed_test_user["id"]))
    async with daily_fire_cap(pod_id, 10):
        result = await _deliver(schedule, _changes(1, 25, WriteIntent.NORMAL))

    assert (result.fired, result.paused, result.skipped) == (10, True, {"fire_cap": 15})
    assert len(await runs(schedule.id)) == 10
    row = await schedule_row(schedule.id)
    assert (row["is_active"], row["paused_reason"]) == (False, "fire_cap")
    assert len(await items("schedule.paused_notice")) == 1
    # One moment, one row in the history: the pause's row is rewritten with
    # the rest of the page, not listed beside it.
    async with async_session_maker() as session:
        from sqlalchemy import text

        skips = (
            (
                await session.execute(
                    text(
                        "SELECT count FROM trigger_skips "
                        "WHERE schedule_id = :s AND reason = 'fire_cap'"
                    ),
                    {"s": schedule.id},
                )
            )
            .scalars()
            .all()
        )
    assert skips == [15], skips


async def test_the_fire_rate_admits_exactly_its_limit_across_concurrent_dispatchers(
    authenticated_client: AsyncClient, fixed_test_org
):
    """650 starts for one trigger, four dispatchers racing: 600 run, 50 wait."""
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    ns = pod_namespace(pod_id)
    key = f"schedule:{uuid4()}"
    async with async_session_maker() as session:
        work = WorkWriter(session)
        for _ in range(650):
            # Reservations that do not exist: each start is a no-op, so this
            # measures the claim's rate and nothing else.
            await work.add(
                SCHEDULE_START, ns, StartTarget(schedule_run_id=uuid4()), rate_key=key
            )
        await session.commit()

    dispatchers = [
        dispatcher_for(
            async_session_maker, WorkClass.AUTOMATION, owner=f"d{n}", slots=64
        )
        for n in range(4)
    ]

    async def drain(dispatcher) -> int:
        taken = 0
        while True:
            claimed = await dispatcher.claim_once()
            if not claimed:
                return taken
            taken += len(claimed)
            await asyncio.gather(*(dispatcher.execute(item) for item in claimed))

    taken = sum(await asyncio.gather(*(drain(d) for d in dispatchers)))

    assert taken == 600
    waiting = await scalar(
        "SELECT count(*) FROM work_items WHERE kind = :kind AND state = 'queued' "
        "AND run_at > now()",
        kind=SCHEDULE_START,
    )
    assert waiting == 50


def _seed_statements() -> list[str]:
    path = (
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-14_change_log_and_flows_0055.py"
    )
    spec = importlib.util.spec_from_file_location("change_log_and_flows_0055", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.trigger_seed_statements()


async def test_cutover_seeds_every_active_time_schedule_and_in_flight_run_once(
    authenticated_client: AsyncClient, fixed_test_org, fixed_test_user
):
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    owner = fixed_test_user["id"]
    now = datetime.now(timezone.utc)

    async def time_row(*, active: bool, cursor: datetime | None, config: str) -> str:
        schedule_id = str(uuid4())
        await execute(
            "INSERT INTO schedules (id, user_id, pod_id, schedule_type, agent_id, config, "
            "instruction, visibility, is_active, is_internal, next_fire_at, "
            "include_outside_rows, consecutive_failures, created_at, updated_at) "
            "VALUES (:id, :uid, :pod, 'TIME', :pod, CAST(:config AS jsonb), 'hi', 'POD', "
            ":active, false, :cursor, false, 0, now(), now())",
            id=schedule_id,
            uid=owner,
            pod=str(pod_id),
            config=config,
            active=active,
            cursor=cursor,
        )
        return schedule_id

    armed = await time_row(
        active=True,
        cursor=now + timedelta(minutes=15),
        config='{"cron": "*/15 * * * *"}',
    )
    legacy = await time_row(active=True, cursor=None, config='{"cron": "*/15 * * * *"}')
    missed = await time_row(
        active=True,
        cursor=None,
        config=f'{{"scheduled_at": "{(now - timedelta(hours=1)).isoformat()}"}}',
    )
    # A cron whose fire the old poller missed hours ago: re-armed from now, not
    # fired late at the cutover.
    late = await time_row(
        active=True,
        cursor=now - timedelta(hours=2),
        config='{"cron": "*/15 * * * *"}',
    )
    inactive = await time_row(
        active=False,
        cursor=now + timedelta(minutes=15),
        config='{"cron": "*/15 * * * *"}',
    )

    async def run_row(
        status: str, started_ago: timedelta | None, *, age: timedelta = timedelta()
    ) -> str:
        run_id = str(uuid4())
        await execute(
            "INSERT INTO schedule_runs (id, schedule_id, user_id, source_event_id, status, "
            "attempts, target_kind, target_run_id, payload, metadata, llm_output, "
            "started_at, created_at, updated_at) VALUES (:id, :sid, :uid, :src, :status, "
            "1, 'AGENT', gen_random_uuid()::text, '{}', '{}', '{}', :started, "
            ":created, now())",
            id=run_id,
            sid=armed,
            uid=owner,
            src=f"src-{run_id}",
            status=status,
            started=None if started_ago is None else now - started_ago,
            created=now - age,
        )
        return run_id

    # In flight on the old path: ended, never started again.
    received = await run_row("RECEIVED", None)
    processing = await run_row("PROCESSING", timedelta(seconds=5))
    old = await run_row("RECEIVED", None, age=timedelta(hours=2))
    done = await run_row("DISPATCHED", timedelta(minutes=10))

    async with async_session_maker() as session:
        from sqlalchemy import text

        for _ in range(2):  # a re-run changes nothing more
            for statement in _seed_statements():
                await session.execute(text(statement))
        await session.commit()

    arms = {item.payload["schedule_id"] for item in await items("schedule.arm")}
    assert arms == {armed, legacy, missed, late}
    assert inactive not in arms
    assert await items(SCHEDULE_START) == [], "the cutover started a run again"

    await run(WorkClass.EVENTS)

    fires = {item.payload["schedule_id"] for item in await items("schedule.fire")}
    assert fires == {armed, legacy, late}
    assert (await schedule_row(late))["next_fire_at"] > now, (
        "a fire already due at the cutover was fired late"
    )
    assert (await schedule_row(legacy))["next_fire_at"] is not None
    retired = await schedule_row(missed)
    assert (retired["is_active"], retired["paused_reason"]) == (
        False,
        "SCHEDULE_ONE_TIME_MISSED",
    )

    statuses = {str(row["id"]): row["status"] for row in await runs(armed)}
    for run_id in (received, processing, old):
        assert statuses[run_id] == "TARGET_FAILED", run_id
    assert statuses[done] == "DISPATCHED"
    # ...which no target of the schedule did: the breaker must not count it,
    # or a schedule with a backlog at the release pauses and emails its owner.
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.schedule.repositories.schedule_run_repository import (
        ScheduleRunRepository,
    )

    async with async_session_maker() as session:
        assert (
            await ScheduleRunRepository(
                SqlAlchemyUnitOfWork(session)
            ).consecutive_terminal_failures(UUID(armed))
            == 0
        )

"""Shared helpers for the change-log e2e tests: real Postgres, real API, real work."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.datastore.changes.cursor import ChangePosition
from app.modules.datastore.changes.handoff import ChangeHandoff
from app.modules.datastore.changes.reader import Change, read_after
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository


async def orders_table(pod_api: DatastoreApi, name: str = "orders") -> UUID:
    created = await pod_api.create_table(
        {
            "name": name,
            "enable_rls": False,
            "columns": [
                {"name": "status", "type": "TEXT"},
                {"name": "total", "type": "INTEGER"},
            ],
        }
    )
    # The log exists once something writes to the pod; tests that append to
    # it directly need it now.
    from app.modules.datastore.infrastructure.pod_schema_migrations import (
        CHANGE_LOG_VERSION,
        migrate_pod_schema,
        schema_name_for_pod,
    )

    await migrate_pod_schema(
        get_datastore_engine(),
        schema_name_for_pod(UUID(pod_api.pod_id)),
        target=CHANGE_LOG_VERSION,
    )
    return UUID(created["id"])


async def datastore_schedule(
    pod_id: UUID, owner: UUID, table: str = "orders", **config: object
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
                config={"table_name": table, **config},
                instruction="Look at the order.",
            )
        )
        await uow.commit()
    return entity


#: ``trigger_fire_day`` high enough that a large test is never capped.
LIFTED_CEILING = {"trigger_fire_day": {"limit": 10_000_000, "per_seconds": 86400}}


@contextmanager
def lifted_daily_ceiling() -> Iterator[None]:
    """New queue rows in this process take the lifted ceiling (a worker
    process gets the same through ``WORK_RATES_JSON``)."""
    import json

    from app.core.infrastructure.work.config import work_settings

    previous = work_settings.work_rates_json
    work_settings.work_rates_json = json.dumps(LIFTED_CEILING)
    try:
        yield
    finally:
        work_settings.work_rates_json = previous


async def raise_daily_ceiling(pod_id: UUID, limit: int = 10_000_000) -> None:
    """Lift ``trigger_fire_day`` on every queue row the pod already has."""
    await execute(
        "INSERT INTO work_queues (class, ns, max_running) VALUES ('events', :ns, 16) "
        "ON CONFLICT (class, ns) DO NOTHING",
        ns=f"pod:{pod_id}",
    )
    await execute(
        "UPDATE work_queues SET rates = COALESCE(rates, '{}'::jsonb) || "
        "jsonb_build_object('trigger_fire_day', "
        "jsonb_build_object('limit', CAST(:limit AS int), 'per_seconds', 86400)) "
        "WHERE ns = :ns",
        ns=f"pod:{pod_id}",
        limit=limit,
    )


async def handoff() -> int:
    return await ChangeHandoff(get_datastore_engine(), async_session_maker).drain()


async def log_final(*, timeout_seconds: float = 60.0) -> None:
    """Wait until every transaction that began before now has ended.

    The log is read below the oldest open transaction on the whole cluster,
    and another test session's transaction can hold that back for a moment.
    """
    from app.modules.test_support.e2e.waiters import eventually

    engine = get_datastore_engine()

    async def snapshot(edge: str) -> int:
        async with engine.connect() as conn:
            value = await conn.scalar(
                text(f"SELECT pg_snapshot_{edge}(pg_current_snapshot())::text")
            )
        return int(value)

    target = await snapshot("xmax")
    await eventually(
        label=f"every transaction before {target} ended",
        probe=lambda: snapshot("xmin"),
        done=lambda xmin: xmin >= target,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.05,
    )


async def deliver_all(*, max_rounds: int = 10_000) -> int:
    """Hand off wakes and run the events class until nothing is due."""
    await log_final()
    total = 0
    while True:
        await handoff()
        ran = await run_until_idle(
            async_session_maker, classes=[WorkClass.EVENTS], max_rounds=max_rounds
        )
        total += ran
        if ran == 0:
            return total


async def deliver_including_late() -> int:
    await make_due(async_session_maker, kind="changes.deliver")
    return await deliver_all()


async def log(pod_id: UUID, *, limit: int = 500) -> list[Change]:
    await log_final()
    changes: list[Change] = []
    cursor: str | None = None
    while True:
        page = await read_after(pod_id, cursor, limit=limit)
        changes.extend(page.changes)
        cursor = page.cursor
        if not page.full:
            return changes


async def execute(sql: str, **params: object) -> None:
    async with async_session_maker() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def scalar(sql: str, **params: object) -> object:
    async with async_session_maker() as session:
        return await session.scalar(text(sql), params)


async def runs(schedule_id: UUID) -> list[Mapping[str, object]]:
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT source_event_id, status, metadata FROM schedule_runs "
                        "WHERE schedule_id = :id ORDER BY created_at, id"
                    ),
                    {"id": str(schedule_id)},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


async def datastore_scalar(sql: str, **params: object) -> object:
    async with get_datastore_engine().connect() as conn:
        return await conn.scalar(text(sql), params)


async def rewind(
    pod_id: UUID, subscriber: str, to: ChangePosition | None = None
) -> None:
    """Put a subscriber's cursor back (to the start of the log by default) and
    wake it: what a crash between a delivery's commit and its cursor move
    leaves behind, at worst."""
    from app.core.infrastructure.work import WorkWriter
    from app.modules.datastore.changes.schema import subscribers_table
    from app.modules.datastore.changes.work import request_delivery
    from app.modules.datastore.infrastructure.pod_schema_migrations import (
        schema_name_for_pod,
    )

    async with get_datastore_engine().begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {subscribers_table(schema_name_for_pod(pod_id))} "
                "SET cursor_xid = CAST(CAST(:xid AS text) AS xid8), cursor_seq = :seq "
                "WHERE subscriber = :key"
            ),
            {
                "key": subscriber,
                "xid": str((to or ChangePosition.start()).xid),
                "seq": (to or ChangePosition.start()).seq,
            },
        )
    async with async_session_maker() as session:
        await request_delivery(WorkWriter(session), pod_id, subscriber)
        await session.commit()

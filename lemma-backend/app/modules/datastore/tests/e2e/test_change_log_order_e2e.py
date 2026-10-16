"""The change log's read rule, under concurrent writers and racing wakes.

A cursor must never skip a slower writer whose lower ``seq`` commits after a
higher one, and a write that commits while a handoff or a delivery is running
must still be delivered.
"""

from __future__ import annotations

import asyncio
import random
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.changes.horizon import delivery_lag
from app.modules.datastore.changes.reader import read_after
from app.modules.datastore.changes.schema import changes_table
from app.modules.datastore.changes.work import DELIVER
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.tests.e2e.change_log_support import (
    datastore_schedule,
    deliver_all,
    deliver_including_late,
    handoff,
    log_final,
    orders_table,
    runs,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


async def _append(conn, pod_id: UUID, table_id: UUID, record_id: str) -> None:
    await conn.execute(
        text(
            f"INSERT INTO {changes_table(schema_name_for_pod(pod_id))} "
            "(table_id, op, record_id) VALUES (CAST(:t AS uuid), 'I', :r)"
        ),
        {"t": str(table_id), "r": record_id},
    )


async def test_a_lower_seq_that_commits_last_is_not_skipped(pod_api: DatastoreApi):
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    engine = get_datastore_engine()

    async with engine.connect() as slow, engine.connect() as fast:
        slow_txn = await slow.begin()
        await _append(slow, pod_id, table_id, "slow")  # lower seq, still open
        async with fast.begin():
            await _append(fast, pod_id, table_id, "fast")  # higher seq, committed

        page = await read_after(pod_id, None)
        assert page.changes == [], "the committed higher seq waits for the slow one"
        cursor = page.cursor

        await slow_txn.commit()

    # Both are final once every transaction older than them has ended, and
    # that horizon is the whole database's: on a shared e2e database another
    # test's transaction can hold it for a moment after this commit.
    page = await eventually(
        label="the slow and fast changes become final",
        probe=lambda: read_after(pod_id, cursor),
        done=lambda read: bool(read.changes),
        timeout_seconds=15,
        interval_seconds=0.1,
    )
    assert [change.record_id for change in page.changes] == ["slow", "fast"]


@pytest.mark.parametrize("round_", range(3))
async def test_concurrent_writers_are_each_read_once_in_order(
    pod_api: DatastoreApi, round_: int
):
    """N writers start, write and commit or abort in random orders while a
    reader keeps reading from its cursor: it sees every committed change
    exactly once, in (xid, seq) order, and no aborted one."""
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api, name=f"orders_{round_}")
    engine = get_datastore_engine()
    rng = random.Random(round_)
    committed: set[str] = set()
    seen: list[str] = []
    positions: list[tuple[int, int]] = []
    done = asyncio.Event()
    # The reader reads again whenever a writer ends, so reads interleave with
    # commits and aborts rather than following them.
    progress = asyncio.Event()

    async def writer(index: int) -> None:
        record = f"w{index}-{uuid4().hex[:6]}"
        async with engine.connect() as conn:
            txn = await conn.begin()
            await asyncio.sleep(rng.random() * 0.05)
            await conn.execute(text("SELECT pg_current_xact_id()"))  # xid now
            await asyncio.sleep(rng.random() * 0.05)
            await _append(conn, pod_id, table_id, record)
            await asyncio.sleep(rng.random() * 0.1)
            if rng.random() < 0.2:
                await txn.rollback()
            else:
                await txn.commit()
                committed.add(record)
        progress.set()

    async def reader() -> None:
        cursor: str | None = None
        while True:
            finished = done.is_set()
            page = await read_after(pod_id, cursor, table_ids=[table_id])
            seen.extend(change.record_id for change in page.changes)
            positions.extend((c.position.xid, c.position.seq) for c in page.changes)
            cursor = page.cursor
            if finished and not page.full:
                return
            if not page.full:
                await progress.wait()
                progress.clear()

    reading = asyncio.create_task(reader())
    await asyncio.gather(*(writer(index) for index in range(40)))
    await log_final()  # another session's open transaction must not end the read early
    done.set()
    progress.set()
    await reading

    assert len(seen) == len(set(seen)), "no change is read twice"
    assert set(seen) == committed, "every committed change is read; no aborted one"
    assert positions == sorted(positions)


@asynccontextmanager
async def _in_flight_insert(pod_id: UUID, table_id: UUID) -> AsyncIterator[None]:
    """A real record write -- row, change and wake row in one statement --
    whose transaction stays open for the block and commits at its end."""
    entity = await _table(table_id)
    ctx = TableContext.from_table_entity(entity, schema_name_for_pod(pod_id))
    capture = ChangeCapture(ctx)
    await capture.prepare()
    async with get_datastore_engine().connect() as conn:
        session = AsyncSession(bind=conn)
        txn = await conn.begin()
        await capture.begin(session)
        sql, params = capture.wrap(
            f'INSERT INTO "{ctx.schema_name}"."orders" (status, total) '
            "VALUES (:status, :total) RETURNING *",
            {"status": "in-flight", "total": 1},
            op="I",
        )
        await session.execute(text(sql), params)
        await capture.wake_handoff(session)
        yield
        await txn.commit()


async def _table(table_id: UUID):
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.datastore.infrastructure.repositories import (
        DatastoreTableRepository,
    )

    async with async_session_maker() as session:
        entity = await DatastoreTableRepository(SqlAlchemyUnitOfWork(session)).get(
            table_id
        )
    assert entity is not None
    return entity


async def test_a_write_during_a_handoff_or_a_delivery_is_still_delivered(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()

    # 1. A write commits while a handoff is running: the handoff takes only
    #    the wake rows it read, and the late write's own row survives it.
    await pod_api.create_record("orders", {"status": "before", "total": 1})
    async with _in_flight_insert(pod_id, table_id):
        await handoff()
    # 2. A write commits while a delivery is running: the delivery's cursor
    #    stops below it (it was not final when the page was read), and its
    #    wake brings the next delivery.
    async with _in_flight_insert(pod_id, table_id):
        await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    await deliver_all()

    fired = await runs(schedule.id)
    assert len(fired) == 3
    assert len({run["source_event_id"] for run in fired}) == 3


async def test_a_change_behind_an_older_open_transaction_is_delivered_once_it_ends(
    pod_api: DatastoreApi, fixed_test_user
):
    """A delivery that runs while an older transaction is open reads nothing:
    the committed change is above the horizon. Its wake was spent on that
    read and no other write is owed, so the delivery must look again itself
    -- else the change waits for the pod's next write, or the subscriber's
    two-minute catch-up."""
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()

    async with get_datastore_engine().connect() as older:
        older_txn = await older.begin()
        await older.execute(text("SELECT pg_current_xact_id()"))  # holds xmin
        await pod_api.create_record("orders", {"status": "behind", "total": 1})
        await handoff()
        await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
        assert await runs(schedule.id) == [], "not final while the older one is open"
        await older_txn.rollback()

    # The delivery asked to look again after a backoff; pull it forward.
    await make_due(async_session_maker, kind=DELIVER)
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    assert len(await runs(schedule.id)) == 1


async def test_a_long_open_transaction_backs_deliveries_off(
    pod_api: DatastoreApi, fixed_test_user
):
    """While an older transaction holds the horizon, each empty look waits
    longer than the last (1, 2, 4, 8, then 15 s): one long transaction must
    not become an item a second for every subscriber behind it. Each look
    notes how long the waiting change has waited, for the lag gauge."""
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_including_late()
    subscriber = f"schedule:{schedule.id}"

    async with get_datastore_engine().connect() as older:
        older_txn = await older.begin()
        await older.execute(text("SELECT pg_current_xact_id()"))  # holds xmin
        await pod_api.create_record("orders", {"status": "behind", "total": 1})
        await handoff()
        waits: list[float] = []
        for _ in range(6):
            await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
            [(not_final, wait)] = await _queued_delivery(subscriber)
            waits.append(round(wait))
            await make_due(async_session_maker, kind=DELIVER)
        assert waits == [1, 2, 4, 8, 15, 15]
        assert not_final == 6
        assert delivery_lag() > 0
        await older_txn.rollback()

    await make_due(async_session_maker, kind=DELIVER)
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    assert len(await runs(schedule.id)) == 1
    assert await _queued_delivery(subscriber) == []


async def _queued_delivery(subscriber: str) -> list[tuple[int, float]]:
    async with async_session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT (payload->>'not_final')::int, "
                "EXTRACT(EPOCH FROM run_at - now()) FROM work_items "
                "WHERE kind = :kind AND state = 'queued' "
                "AND payload->>'subscriber' = :subscriber "
                "AND dedup_key NOT LIKE :late"
            ),
            {"kind": DELIVER, "subscriber": subscriber, "late": "%:late"},
        )
        return [(int(row[0]), float(row[1])) for row in rows]


async def test_positions_order_by_number_not_by_their_text(pod_api: DatastoreApi):
    """Transaction ids crossing a digit boundary (999, 1000) still page in
    order: a read ordered by the ids' text would put 1000 first and skip 999."""
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    async with get_datastore_engine().begin() as conn:
        for xid, record in ((1000, "later"), (999, "earlier"), (99, "earliest")):
            await conn.execute(
                text(
                    f"INSERT INTO {changes_table(schema_name_for_pod(pod_id))} "
                    "(xid, table_id, op, record_id) "
                    "VALUES (CAST(CAST(:xid AS text) AS xid8), CAST(:t AS uuid), 'I', :r)"
                ),
                {"xid": str(xid), "t": str(table_id), "r": record},
            )
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        page = await read_after(pod_id, cursor, limit=1)
        seen.extend(change.record_id for change in page.changes)
        cursor = page.cursor
    assert seen == ["earliest", "earlier", "later"]

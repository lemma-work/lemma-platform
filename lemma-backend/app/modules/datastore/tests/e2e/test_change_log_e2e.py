"""The record change log, end to end: API writes, the log, DATASTORE triggers.

Real Postgres for both databases, the real record API, and the real work
runtime running the ``changes.*`` kinds in process.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.datastore.changes.cursor import decode_cursor
from app.modules.datastore.changes.reader import ChangeOp, read_after
from app.modules.datastore.changes.schema import PENDING_TABLE
from app.modules.datastore.tests.e2e.change_log_support import (
    change_log_mode,
    datastore_scalar,
    datastore_schedule,
    deliver_all,
    log,
    orders_table,
    rewind,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


@pytest.fixture(autouse=True)
def _change_log_on():
    with change_log_mode("on"):
        yield


async def test_unwatched_writes_append_slim_rows_and_wake_nobody(pod_api: DatastoreApi):
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)

    created = await pod_api.create_record("orders", {"status": "new", "total": 1})
    await pod_api.update_record("orders", created["id"], {"status": "paid"})
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(3)]
    )
    await pod_api.delete_record("orders", created["id"])

    changes = await log(pod_id)
    assert [change.op for change in changes] == [
        ChangeOp.INSERT,
        ChangeOp.UPDATE,
        ChangeOp.INSERT,
        ChangeOp.INSERT,
        ChangeOp.INSERT,
        ChangeOp.DELETE,
    ]
    assert {change.table_id for change in changes} == {table_id}
    assert [change.intent for change in changes] == [
        "normal",
        "normal",
        "bulk",
        "bulk",
        "bulk",
        "normal",
    ]
    assert changes[0].record_id == created["id"] == changes[-1].record_id
    # Nothing watches the table: no snapshot, no wake row.
    assert all(c.new_row is None and c.old_values is None for c in changes)
    assert (
        await datastore_scalar(
            f"SELECT count(*) FROM {PENDING_TABLE} WHERE pod_id = CAST(:pod AS uuid)",
            pod=str(pod_id),
        )
        == 0
    )
    # A cursor after the last change reads nothing, and is stable.
    page = await read_after(pod_id, changes[-1].cursor)
    assert page.changes == [] and not page.full


async def test_a_watched_table_logs_snapshots_and_each_change_fires_once(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id,
        UUID(fixed_test_user["id"]),
        operations=["INSERT", "UPDATE"],
        when={"status": {"equals": "paid"}},
    )
    await deliver_all()  # the sync: the subscriber row now exists

    paid = await pod_api.create_record("orders", {"status": "paid", "total": 5})
    pending = await pod_api.create_record("orders", {"status": "new", "total": 7})
    await pod_api.update_record("orders", pending["id"], {"status": "paid"})
    await pod_api.delete_record("orders", paid["id"])  # not watched: slim

    changes = await log(pod_id)
    insert, _, update, delete = changes
    assert insert.new_row is not None and insert.new_row["status"] == "paid"
    assert update.old_values == {"status": "new"}
    assert update.new_row is not None and update.new_row["status"] == "paid"
    assert delete.new_row is None and delete.old_values is None

    await deliver_all()
    fired = [run["source_event_id"] for run in await runs(schedule.id)]
    assert fired == [f"chg:{insert.position}", f"chg:{update.position}"]

    # Delivered again from the start (a crash before the cursor moved): the
    # run keys absorb it.
    await rewind(pod_id, f"schedule:{schedule.id}")
    await deliver_all()
    assert [run["source_event_id"] for run in await runs(schedule.id)] == fired


async def test_bulk_writes_fire_nothing_by_default_and_are_recorded_as_skipped(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()

    for offset in range(0, 1200, 400):
        await pod_api.bulk_create(
            "orders",
            [{"status": "new", "total": n} for n in range(offset, offset + 400)],
        )
    await deliver_all()

    assert await runs(schedule.id) == []
    assert (
        await scalar(
            "SELECT sum(count) FROM trigger_skips WHERE schedule_id = :id "
            "AND reason = 'bulk_write'",
            id=str(schedule.id),
        )
        == 1200
    )
    # Aggregated: one row per page of changes, not one per change.
    assert (
        await scalar(
            "SELECT count(*) FROM trigger_skips WHERE schedule_id = :id",
            id=str(schedule.id),
        )
        <= 3
    )


async def test_current_rows_are_fetched_by_the_primary_key_index(pod_api: DatastoreApi):
    """A live reader's per-batch row fetch is an index lookup, not a scan of
    the table; comparing the key as text could only ever be the scan."""
    import json

    from sqlalchemy import text

    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.datastore.changes.reader import fetch_rows, fetch_rows_statement
    from app.modules.datastore.infrastructure.pod_schema_migrations import (
        schema_name_for_pod,
    )
    from app.modules.datastore.infrastructure.repositories import (
        DatastoreTableRepository,
    )
    from app.modules.datastore.infrastructure.session import get_datastore_engine
    from app.modules.datastore.services.table_context import TableContext

    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    await pod_api.bulk_create(
        "orders", [{"status": "s", "total": n} for n in range(200)]
    )
    wanted = await pod_api.create_record("orders", {"status": "wanted", "total": 1})
    async with async_session_maker() as session:
        entity = await DatastoreTableRepository(SqlAlchemyUnitOfWork(session)).get(
            table_id
        )
    assert entity is not None
    ctx = TableContext.from_table_entity(entity, schema_name_for_pod(pod_id))

    rows = await fetch_rows(
        pod_id, ctx, [wanted["id"], "00000000-0000-0000-0000-000000000000"]
    )
    assert list(rows) == [wanted["id"]] and rows[wanted["id"]]["status"] == "wanted"

    sql, params = fetch_rows_statement(ctx, [wanted["id"]])
    async with get_datastore_engine().connect() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            await conn.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"), params)
        ).scalar_one()
    assert "Index" in json.dumps(plan) and "Seq Scan" not in json.dumps(plan), plan


async def test_the_live_wake_names_the_writing_transaction(pod_api: DatastoreApi):
    """A hub told the writer's xid can wait for the horizon to pass it rather
    than re-read an empty page while an older transaction is open."""
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis
    from app.modules.datastore.changes.capture import live_channel, parse_wake

    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    pubsub = get_redis(url=settings.redis_url).pubsub()
    await pubsub.subscribe(live_channel(pod_id))
    try:
        await pod_api.create_record("orders", {"status": "new", "total": 1})
        message = await eventually(
            label="the live wake",
            probe=lambda: pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            ),
            done=lambda received: received is not None,
            timeout_seconds=5,
        )
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
    xid = parse_wake(message["data"])
    [change] = await log(pod_id)
    assert xid is not None and xid == change.position.xid
    # Readable exactly when a read's horizon is past the named transaction.
    page = await read_after(pod_id, None)
    assert decode_cursor(page.horizon).xid > xid and page.changes == [change]

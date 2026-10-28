"""The change log over time: retention, renames, batch replays, write intent,
and the cutover that converts what the old event path still held."""

from __future__ import annotations


from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    WorkWriter,
)
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.datastore.changes.reader import ChangeOp, read_after
from app.modules.datastore.changes.retention import (
    _schemas_with_log,
    retain,
    retain_pod,
)
from app.modules.datastore.changes.schema import changes_table, subscribers_table
from app.modules.datastore.changes.subscribers import SubscriberTable
from app.modules.datastore.changes.work import register_change_work
from app.modules.datastore.infrastructure.pod_schema_catalog import drop_pod_schema
from app.modules.datastore.infrastructure.pod_schema_migrations import (
    VERSIONS_TABLE,
    schema_name_for_pod,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.change_log_support import (
    datastore_schedule,
    deliver_all,
    deliver_including_late,
    execute,
    log,
    orders_table,
    rewind,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


async def _age(pod_id: UUID, *, hours: int) -> None:
    async with get_datastore_engine().begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {changes_table(schema_name_for_pod(pod_id))} "
                "SET at = now() - make_interval(hours => :hours)"
            ),
            {"hours": hours},
        )


async def test_retention_keeps_what_a_durable_subscriber_has_not_read(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    for n in range(10):
        await pod_api.create_record("orders", {"status": "new", "total": n})
    first_cursor = (await read_after(pod_id, None, limit=1)).changes[0].cursor
    await _age(pod_id, hours=2)

    subscribers = await SubscriberTable(pod_id).all()
    assert await retain(pod_id, subscribers) == 0, "unread changes are never deleted"
    assert len(await log(pod_id)) == 10

    await deliver_all()
    assert len(await runs(schedule.id)) == 10
    subscribers = await SubscriberTable(pod_id).all()
    assert await retain(pod_id, subscribers) == 10
    assert await log(pod_id) == []
    # A live reader whose cursor sat below what was deleted is told to resync.
    assert (await read_after(pod_id, first_cursor)).expired


async def test_retention_keeps_the_window_for_a_pod_nobody_subscribes_to(
    pod_api: DatastoreApi,
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    for n in range(3):
        await pod_api.create_record("orders", {"status": "old", "total": n})
    await _age(pod_id, hours=2)
    await pod_api.create_record("orders", {"status": "recent", "total": 9})

    assert await retain(pod_id, []) == 3
    [kept] = await log(pod_id)
    assert kept.at > datetime.now(timezone.utc) - timedelta(minutes=5)


async def _version_row(schema: str) -> int | None:
    async with get_datastore_engine().connect() as conn:
        return await conn.scalar(
            text(f"SELECT version FROM {VERSIONS_TABLE} WHERE schema_name = :name"),
            {"name": schema},
        )


async def test_retention_passes_over_a_pod_whose_schema_is_gone(
    pod_api: DatastoreApi,
):
    """A deleted pod's leftover version row is not retention's to fail on.

    Before a drop forgot the row, one deleted pod failed the hourly page it
    was listed in, so every pod after it on that page went unretained.
    """
    pod_id = UUID(pod_api.pod_id)
    schema = schema_name_for_pod(pod_id)
    await orders_table(pod_api)
    await pod_api.create_record("orders", {"status": "new", "total": 1})
    assert schema in await _schemas_with_log(""), "precondition: a live log is listed"

    # How a deleted pod looked before `drop_pod_schema` removed its version
    # row: the schema gone, the row still there.
    async with get_datastore_engine().begin() as conn:
        await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    assert await _version_row(schema) is not None

    assert schema not in await _schemas_with_log("")
    # And a pod that goes between the listing and its pass is skipped too.
    assert await retain(pod_id, []) == 0


async def test_dropping_a_pod_schema_forgets_its_version(pod_api: DatastoreApi):
    pod_id = UUID(pod_api.pod_id)
    schema = schema_name_for_pod(pod_id)
    await orders_table(pod_api)
    assert await _version_row(schema) is not None

    async with get_datastore_engine().begin() as conn:
        await drop_pod_schema(conn, schema)

    assert await _version_row(schema) is None


_TEND = "test.changes.retain_pod"


class _Pod(BaseModel):
    pod_id: UUID


async def _retain_pod(pod_id: UUID) -> int:
    """One pod's retention pass, run as a real job: the periodic job walks
    every pod schema in the database, which a shared test database has many
    of."""
    deleted: list[int] = []

    async def run(ctx: JobContext, payload: _Pod) -> None:
        deleted.append(await retain_pod(ctx, payload.pod_id))

    registry = WorkRegistry()
    register_change_work(registry)
    registry.handler(kind=_TEND, cls=WorkClass.ADMIN, mode=WorkMode.JOB, payload=_Pod)(
        run
    )
    async with async_session_maker() as session:
        await WorkWriter(session, registry=registry).add(
            _TEND, SYSTEM_NAMESPACE, _Pod(pod_id=pod_id)
        )
        await session.commit()
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.ADMIN]
    )
    [count] = deleted
    return count


async def test_a_trigger_on_a_quiet_table_does_not_keep_a_busy_tables_log(
    pod_api: DatastoreApi, fixed_test_user
):
    """Retention keeps what a subscriber has not read; a trigger on a table
    nobody writes has read everything it watches, so it moves to the horizon
    and the busy table's changes go once the window passes."""
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api, name="quiet")
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), table="quiet", operations=["INSERT"]
    )
    await deliver_including_late()
    for n in range(20):
        await pod_api.create_record("orders", {"status": "busy", "total": n})
    busy = await log(pod_id)
    await _age(pod_id, hours=2)

    assert await _retain_pod(pod_id) == 20
    assert await log(pod_id) == []
    [row] = await SubscriberTable(pod_id).all()
    assert row.subscriber == f"schedule:{schedule.id}"
    assert row.cursor > busy[-1].position


async def test_a_trigger_too_far_behind_is_lapsed_and_says_what_it_skipped(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_including_late()
    for n in range(3):
        await pod_api.create_record("orders", {"status": "stuck", "total": n})
    changes = await log(pod_id)
    # Older than the lag ceiling, and never delivered.
    await _age(pod_id, hours=25)

    await _retain_pod(pod_id)
    skips = await _skips(schedule.id)
    assert skips == [
        ("lag_exceeded", str(changes[0].position), str(changes[-1].position), 3)
    ]
    assert await log(pod_id) == [], "past the window and no longer owed"
    await deliver_all()
    assert await runs(schedule.id) == [], "the lapsed changes never fire"
    # A later write is delivered as usual: the trigger lapsed, it did not stop.
    await pod_api.create_record("orders", {"status": "fresh", "total": 9})
    await deliver_all()
    assert len(await runs(schedule.id)) == 1


async def test_an_unknown_subscriber_is_dropped_only_once_it_stops_moving(
    pod_api: DatastoreApi,
):
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    subscribers = SubscriberTable(pod_id)
    for key in ("ghost:idle", "ghost:moving"):
        await subscribers.upsert(
            key,
            table_id=table_id,
            ops=frozenset({ChangeOp.INSERT}),
            delay=timedelta(0),
        )
    async with get_datastore_engine().begin() as conn:
        await conn.execute(
            text(
                f"UPDATE {subscribers_table(schema_name_for_pod(pod_id))} "
                "SET updated_at = now() - interval '25 hours' "
                "WHERE subscriber = 'ghost:idle'"
            )
        )

    await _retain_pod(pod_id)
    assert [row.subscriber for row in await subscribers.all()] == ["ghost:moving"]


async def _skips(schedule_id: UUID) -> list[tuple[str, str, str, int]]:
    async with async_session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT reason, first, last, count FROM trigger_skips "
                "WHERE schedule_id = :id ORDER BY at"
            ),
            {"id": str(schedule_id)},
        )
        return [(str(r[0]), str(r[1]), str(r[2]), int(r[3])) for r in rows]


async def test_a_renamed_table_keeps_its_subscriber(
    pod_api: DatastoreApi, fixed_test_user
):
    """Subscribers and changes name a table by id, not by name."""
    pod_id = UUID(pod_api.pod_id)
    table_id = await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    schema = schema_name_for_pod(pod_id)
    async with get_datastore_engine().begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{schema}".orders RENAME TO purchases'))
    await execute(
        "UPDATE datastore_tables SET table_name = 'purchases' WHERE id = :id",
        id=str(table_id),
    )

    await pod_api.create_record("purchases", {"status": "new", "total": 1})
    await deliver_all()
    [change] = await log(pod_id)
    assert change.table_id == table_id
    assert [run["source_event_id"] for run in await runs(schedule.id)] == [
        f"chg:{change.position}"
    ]


async def test_a_bulk_write_may_ask_to_fire_like_single_writes(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    response = await pod_api.request(
        "POST",
        f"/pods/{pod_id}/datastore/tables/orders/records/bulk/create",
        json={
            "records": [{"status": "new", "total": n} for n in range(4)],
            "intent": "normal",
        },
    )
    assert response.status_code == 200, response.text
    await deliver_all()
    assert len(await runs(schedule.id)) == 4


async def test_a_trigger_told_to_fire_on_bulk_writes_gets_their_rows_at_once(
    pod_api: DatastoreApi, fixed_test_user
):
    """A bulk write a trigger skipped was logged without its rows. Told to fire
    on bulk writes before its subscriber re-syncs, it must still be handed the
    rows -- not an empty payload its conditions run against."""
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"], bulk_writes="skip"
    )
    await deliver_all()
    response = await pod_api.request(
        "POST",
        f"/pods/{pod_id}/datastore/tables/orders/records/bulk/create",
        json={"records": [{"status": "new", "total": 7}], "intent": "bulk"},
    )
    assert response.status_code == 200, response.text
    # The config changes; nothing re-syncs the subscriber's stored intents.
    await execute(
        'UPDATE schedules SET config = config || \'{"bulk_writes": "each"}\' '
        "WHERE id = :id",
        id=schedule.id,
    )
    await deliver_all()
    assert len(await runs(schedule.id)) == 1
    total = await scalar(
        "SELECT payload->>'total' FROM schedule_runs WHERE schedule_id = :id",
        id=schedule.id,
    )
    assert total == "7", "the trigger fired on an empty row"


async def test_a_batch_trigger_never_fires_a_window_twice_on_replay(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    schedule = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"], bulk_writes="batch"
    )
    await deliver_including_late()
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(30)]
    )
    await deliver_including_late()  # the window delay, pulled forward
    [first] = await runs(schedule.id)
    assert first["source_event_id"].startswith(f"batch:{schedule.id}:")

    # Replayed after a crash, with more changes now readable after the window:
    # the planned window is not fired again; what follows is its own batch.
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(5)]
    )
    await rewind(pod_id, f"schedule:{schedule.id}")
    await deliver_including_late()
    fired = await runs(schedule.id)
    assert len(fired) == 2
    assert fired[0] == first
    assert (
        await scalar(
            "SELECT sum(count) FROM trigger_batches WHERE schedule_id = :id",
            id=str(schedule.id),
        )
        == 35
    )

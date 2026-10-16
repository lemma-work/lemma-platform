"""Rows are logged only for the writes some subscriber acts on.

A bulk write every watching trigger skips is logged slim -- it still wakes them,
so they record the skip -- and a subscriber that wants that intent brings the
rows back. Real Postgres, the real record API, the work runtime in process.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.datastore.changes.schema import PENDING_TABLE
from app.modules.datastore.tests.e2e.change_log_support import (
    change_log_mode,
    datastore_scalar,
    datastore_schedule,
    deliver_all,
    log,
    orders_table,
    runs,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


@pytest.fixture(autouse=True)
def _change_log_on():
    with change_log_mode("on"):
        yield


async def _skipped(schedule_id: UUID) -> object:
    return await scalar(
        "SELECT coalesce(sum(count), 0) FROM trigger_skips "
        "WHERE schedule_id = :id AND reason = 'bulk_write'",
        id=str(schedule_id),
    )


async def _pending(pod_id: UUID) -> object:
    return await datastore_scalar(
        f"SELECT count(*) FROM {PENDING_TABLE} WHERE pod_id = CAST(:pod AS uuid)",
        pod=str(pod_id),
    )


async def test_a_bulk_write_every_watcher_skips_is_logged_slim_and_still_skipped(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    owner = UUID(fixed_test_user["id"])
    await orders_table(pod_api)
    skipping = await datastore_schedule(pod_id, owner, operations=["INSERT"])
    await deliver_all()

    single = await pod_api.create_record("orders", {"status": "new", "total": 1})
    await pod_api.bulk_create(
        "orders", [{"status": "new", "total": n} for n in range(50)]
    )
    assert await _pending(pod_id) > 0  # watched: the skipping trigger is woken

    changes = await log(pod_id)
    [normal] = [c for c in changes if c.intent == "normal"]
    bulk = [c for c in changes if c.intent == "bulk"]
    assert normal.record_id == single["id"] and normal.new_row is not None
    assert len(bulk) == 50
    assert all(c.new_row is None and c.old_values is None for c in bulk)

    await deliver_all()
    assert await _skipped(skipping.id) == 50
    assert [run["source_event_id"] for run in await runs(skipping.id)] == [
        f"chg:{normal.position}"
    ]

    # A second trigger that fires each bulk change: those rows are logged again.
    each = await datastore_schedule(
        pod_id, owner, operations=["INSERT"], bulk_writes="each"
    )
    await deliver_all()
    await pod_api.bulk_create(
        "orders", [{"status": "paid", "total": n} for n in range(3)]
    )
    later = [c for c in await log(pod_id) if c.intent == "bulk"][50:]
    assert len(later) == 3
    assert all(c.new_row is not None and c.new_row["status"] == "paid" for c in later)

    await deliver_all()
    assert await _skipped(skipping.id) == 53
    each_runs = await runs(each.id)
    assert [run["source_event_id"] for run in each_runs] == [
        f"chg:{c.position}" for c in later
    ]


async def test_changing_the_pod_default_brings_bulk_rows_back(
    pod_api: DatastoreApi, fixed_test_user
):
    pod_id = UUID(pod_api.pod_id)
    await orders_table(pod_api)
    # No `bulk_writes`: the trigger follows the pod's default, which is skip.
    inheriting = await datastore_schedule(
        pod_id, UUID(fixed_test_user["id"]), operations=["INSERT"]
    )
    await deliver_all()
    await pod_api.bulk_create("orders", [{"status": "new", "total": 1}])
    assert all(c.new_row is None for c in await log(pod_id))
    await deliver_all()
    assert await _skipped(inheriting.id) == 1

    updated = await pod_api.request(
        "PUT",
        f"/pods/{pod_api.pod_id}",
        json={"config": {"triggers": {"bulk_writes_default": "each"}}},
    )
    assert updated.status_code == 200, updated.text
    await deliver_all()  # the resync the setting change wrote

    await pod_api.bulk_create("orders", [{"status": "paid", "total": 2}])
    newest = (await log(pod_id))[-1]
    assert newest.new_row is not None and newest.new_row["status"] == "paid"
    await deliver_all()
    assert [run["source_event_id"] for run in await runs(inheriting.id)] == [
        f"chg:{newest.position}"
    ]

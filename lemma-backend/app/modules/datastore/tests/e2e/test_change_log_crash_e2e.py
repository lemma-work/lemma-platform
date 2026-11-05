"""A 100,000-row import into a watched table, delivered across a worker SIGKILL.

A real worker (``python -m app.worker``, events class only) delivers the
change log; it is killed with SIGKILL partway and a second one finishes. Every
matching change fires its ``each`` trigger exactly once, and the ``batch``
trigger's planned windows never overlap, never repeat and cover every change.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from uuid import UUID

import pytest

from app.modules.datastore.tests.e2e.change_log_support import (
    LIFTED_CEILING,
    lifted_daily_ceiling,
    datastore_schedule,
    deliver_all,
    log,
    orders_table,
    raise_daily_ceiling,
    scalar,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.asyncio,
    pytest.mark.drives_work,
    # A hundred thousand fires, one by one: 236-242s on a CI runner and a
    # laptop alike, at its last green commit as on main -- so close to
    # the suite-wide 300s that a slower runner crossed it. Its own waits
    # allow far more; this is a backstop for a hang, not a delivery budget.
    pytest.mark.timeout(600),
]

ROWS = 100_000
PER_REQUEST = 1_000

_WORKER_ENV = {
    "WORK_CLASSES": "events",
    "WORK_POLL_INTERVAL_SECONDS": "0.2",
    "WORK_RATES_JSON": json.dumps(LIFTED_CEILING),
}


async def _import(pod_api: DatastoreApi) -> None:
    async def one(start: int) -> None:
        await pod_api.bulk_create(
            "orders",
            [
                {"status": "imported", "total": n}
                for n in range(start, start + PER_REQUEST)
            ],
        )

    for wave in range(0, ROWS, PER_REQUEST * 10):
        await asyncio.gather(
            *(one(start) for start in range(wave, wave + PER_REQUEST * 10, PER_REQUEST))
        )


async def _fires(schedule_id: UUID) -> int:
    return int(
        await scalar(  # type: ignore[arg-type]
            "SELECT count(*) FROM schedule_runs WHERE schedule_id = :id",
            id=str(schedule_id),
        )
    )


async def test_a_large_import_fires_each_change_once_across_a_worker_kill(
    pod_api: DatastoreApi, fixed_test_user, e2e_settings
):
    pod_id = UUID(pod_api.pod_id)
    owner = UUID(fixed_test_user["id"])
    with lifted_daily_ceiling():
        await orders_table(pod_api)
        each = await datastore_schedule(
            pod_id, owner, operations=["INSERT"], bulk_writes="each"
        )
        batch = await datastore_schedule(
            pod_id, owner, operations=["INSERT"], bulk_writes="batch"
        )
        await raise_daily_ceiling(pod_id)
        await deliver_all()  # both subscribers exist before the import

        started = time.monotonic()
        await _import(pod_api)
        imported_in = time.monotonic() - started
        # Final once every older transaction on the cluster has ended, which
        # another session's open transaction can hold back for a moment.
        changes = await eventually(
            label="the whole import is readable",
            probe=lambda: log(pod_id),
            done=lambda read: len(read) == ROWS,
            timeout_seconds=120,
            interval_seconds=1.0,
        )
        positions = {f"chg:{change.position}" for change in changes}
        assert len(positions) == ROWS

        async with production_worker_process(
            e2e_settings, log_prefix="lemma_change_log_kill_1", extra_env=_WORKER_ENV
        ) as first:
            await eventually(
                label="the first worker is part-way through",
                probe=lambda: _fires(each.id),
                done=lambda n: n >= ROWS // 5,
                timeout_seconds=600,
                interval_seconds=0.5,
                fail_fast=lambda _: (
                    first.read_log_tail() if first.process.poll() else None
                ),
            )
            first.process.send_signal(signal.SIGKILL)
            killed_at = await _fires(each.id)

        delivery_started = time.monotonic()
        async with production_worker_process(
            e2e_settings, log_prefix="lemma_change_log_kill_2", extra_env=_WORKER_ENV
        ) as second:
            await eventually(
                label="every change fired its each trigger",
                probe=lambda: _fires(each.id),
                done=lambda n: n >= ROWS,
                timeout_seconds=1_200,
                interval_seconds=1.0,
                fail_fast=lambda _: (
                    second.read_log_tail() if second.process.poll() else None
                ),
            )
            await eventually(
                label="the batch trigger's windows cover every change",
                probe=lambda: scalar(
                    "SELECT coalesce(sum(count), 0) FROM trigger_batches WHERE schedule_id = :id",
                    id=str(batch.id),
                ),
                done=lambda n: int(n) >= ROWS,  # type: ignore[call-overload]
                timeout_seconds=600,
                interval_seconds=1.0,
            )
            await asyncio.sleep(2)  # anything still in flight would show now
        finished_in = time.monotonic() - delivery_started

    fired = await scalar(
        "SELECT count(DISTINCT source_event_id) FROM schedule_runs WHERE schedule_id = :id",
        id=str(each.id),
    )
    assert fired == await _fires(each.id) == ROWS
    assert (
        await scalar(
            "SELECT count(*) FROM schedule_runs WHERE schedule_id = :id "
            "AND NOT (source_event_id = ANY(CAST(:keys AS text[])))",
            id=str(each.id),
            keys=sorted(positions),
        )
        == 0
    ), "every fire is one of the imported changes"

    windows = await scalar(
        "SELECT count(*) FROM trigger_batches WHERE schedule_id = :id", id=str(batch.id)
    )
    batch_fires = await _fires(batch.id)
    assert batch_fires == windows, "one fire per planned window, never two"
    assert (
        await scalar(
            "SELECT sum(count) FROM trigger_batches WHERE schedule_id = :id",
            id=str(batch.id),
        )
        == ROWS
    ), "windows are disjoint and cover every change"
    print(
        f"imported {ROWS} rows in {imported_in:.1f}s; killed at {killed_at} fires; "
        f"second worker finished in {finished_in:.1f}s; {windows} batch windows"
    )

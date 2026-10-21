"""The change log's pure parts: delivery backoff, the lag gauge's windows, the
transaction id a captured write returns, and the bounded live wake."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4


from app.modules.datastore.changes import capture as capture_module
from app.modules.datastore.changes.capture import ChangeCapture
from app.modules.datastore.changes.horizon import LAG_WINDOW_SECONDS, _LagWindows
from app.modules.datastore.changes.work import NOT_FINAL_BACKOFF, not_final_retry
from app.modules.datastore.services.table_context import TableContext


def test_a_delivery_behind_an_open_transaction_backs_off_to_fifteen_seconds():
    waits = [not_final_retry(step).total_seconds() for step in range(8)]
    assert waits == [1, 2, 4, 8, 15, 15, 15, 15]
    assert not_final_retry(-1) == NOT_FINAL_BACKOFF[0]
    assert NOT_FINAL_BACKOFF[-1] == timedelta(seconds=15)


def test_the_lag_gauge_reads_the_worst_of_this_window_and_the_last():
    lag = _LagWindows()
    lag.note(5.0, now=1000.0)
    lag.note(2.0, now=1010.0)
    assert lag.reading(now=1020.0) == 5.0
    # The next window starts low; the last one's worst still shows.
    lag.note(1.0, now=1000.0 + LAG_WINDOW_SECONDS + 1)
    assert lag.reading(now=1000.0 + LAG_WINDOW_SECONDS + 2) == 5.0
    # A window with nothing worse, then nothing at all: it falls away.
    assert lag.reading(now=1000.0 + 2 * LAG_WINDOW_SECONDS + 2) == 1.0
    assert lag.reading(now=1000.0 + 5 * LAG_WINDOW_SECONDS) == 0.0


def _ctx() -> TableContext:
    return TableContext(
        pod_id=uuid4(),
        table_id=uuid4(),
        table_name="orders",
        schema_name="pod_x",
        columns=[],
        primary_key_column="id",
        enable_rls=False,
    )


def test_a_captured_write_returns_its_transaction_id_with_its_rows():
    capture = ChangeCapture(_ctx())
    statement, _ = capture.wrap(
        'INSERT INTO "pod_x"."orders" (id) VALUES (:id) RETURNING *',
        {"id": "a"},
        op="I",
    )
    assert statement.endswith(
        'SELECT *, pg_current_xact_id()::text AS "__lemma_xid" FROM w'
    )
    row = SimpleNamespace(_mapping={"id": "a", "__lemma_xid": "812"})
    assert capture.row(row) == {"id": "a"}  # type: ignore[arg-type]
    assert capture.xid == "812"


def test_a_counted_write_returns_its_transaction_id_with_its_count():
    capture = ChangeCapture(_ctx())
    statement, _ = capture.wrap(
        'INSERT INTO "pod_x"."orders" (id) VALUES (:id) RETURNING *',
        {"id": "a"},
        op="I",
        returning="count",
    )
    assert statement.endswith("SELECT count(*), pg_current_xact_id()::text FROM w")
    result = SimpleNamespace(one=lambda: (3, "913"))
    assert capture.count(result) == 3  # type: ignore[arg-type]
    assert capture.xid == "913"


async def test_the_live_wake_never_waits_and_drops_past_its_bound(monkeypatch):
    release = asyncio.Event()
    published: list[str] = []

    async def slow_publish(pod_id, xid) -> None:
        await release.wait()
        published.append(xid)

    dropped: list[int] = []
    monkeypatch.setattr(capture_module, "_publish", slow_publish)
    monkeypatch.setattr(capture_module, "MAX_PENDING_WAKES", 2)
    monkeypatch.setattr(
        capture_module,
        "wakes_dropped",
        SimpleNamespace(add=lambda n, *a: dropped.append(n)),
    )
    capture = ChangeCapture(_ctx())
    capture.wrap("INSERT INTO t VALUES (1) RETURNING *", {}, op="I")
    capture.xid = "7"

    for _ in range(3):
        capture.publish_wake()  # returns at once, Redis or no Redis
    assert dropped == [1]
    release.set()
    await asyncio.sleep(0)
    await asyncio.gather(*list(capture_module._pending_wakes))
    await asyncio.sleep(0)  # the done callbacks that let go of them
    assert published == ["7", "7"]
    assert not capture_module._pending_wakes


def test_a_capture_that_wrote_nothing_wakes_nobody():
    capture = ChangeCapture(_ctx())
    capture.publish_wake()
    assert not capture_module._pending_wakes

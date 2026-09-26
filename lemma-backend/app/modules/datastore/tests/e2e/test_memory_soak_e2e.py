"""Memory soak: traffic shapes that once retained memory must now retain none.

Each scenario warms up, then replays the shape under ``tracemalloc`` and bounds
what is still allocated afterwards. The budgets are loose on purpose -- the
failures they guard against were not subtle. Before the datastore engine lost
its compiled-statement cache, twenty bulk writes of distinct row counts kept
154 MiB alive (one ~15 MB cache entry per 1000x10 statement), and production
API pods climbed gigabytes in steps that never came back down.

What is measured is Python-level retention, not RSS: RSS moves with the
allocator and the test runner, and a leak that lives in Python objects is the
one a test can pin to a cause.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import tracemalloc
import uuid

import pytest

from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.datastore.tests.e2e.test_changes_ws_e2e import _ws_communicator

pytestmark = pytest.mark.e2e

_MIB = 1024 * 1024
_COLUMNS = 10
# Distinct row counts: each would have been a distinct cached statement.
_BULK_SIZES = range(25, 501, 25)


def _row(index: int) -> dict[str, str]:
    return {f"c{column}": f"v{index}-{column}" for column in range(_COLUMNS)}


def _retained_bytes(before: tracemalloc.Snapshot) -> int:
    gc.collect()
    after = tracemalloc.take_snapshot()
    return sum(stat.size_diff for stat in after.compare_to(before, "filename"))


async def _soak_table(pod_api: DatastoreApi) -> None:
    await pod_api.create_table(
        {
            "name": "soak",
            "columns": [
                {"name": f"c{column}", "type": "TEXT", "required": False}
                for column in range(_COLUMNS)
            ],
        }
    )


def test_the_datastore_engine_keeps_no_compiled_statements() -> None:
    """The root cause, pinned directly: every statement on this engine is
    dynamic SQL text, so a compiled cache only ever retains it."""
    assert get_datastore_engine().sync_engine._compiled_cache is None


async def test_bulk_writes_of_varying_size_retain_nothing(
    pod_api: DatastoreApi,
) -> None:
    await _soak_table(pod_api)
    await pod_api.bulk_create("soak", [_row(0)])  # warm every code path once

    tracemalloc.start(1)
    try:
        before = tracemalloc.take_snapshot()
        for size in _BULK_SIZES:
            await pod_api.bulk_create("soak", [_row(i) for i in range(size)])
        rows = (await pod_api.query('SELECT id FROM "soak"'))["items"]
        ids = [row["id"] for row in rows]
        for size in _BULK_SIZES:
            batch = ids[:size]
            await pod_api.bulk_update(
                "soak", [{"id": record_id, "c0": "updated"} for record_id in batch]
            )
        for size in _BULK_SIZES:
            batch, ids = ids[:size], ids[size:]
            if batch:
                await pod_api.bulk_delete("soak", batch)
        retained = _retained_bytes(before)
    finally:
        tracemalloc.stop()

    # 60 bulk statements of 20 shapes. With the old cache this was >150 MiB.
    assert retained < 8 * _MIB, f"bulk writes retained {retained / _MIB:.1f} MiB"


async def _handshake(app, pod_id: str, token: str, *, leave_after_ready: bool) -> None:
    communicator = _ws_communicator(app, pod_id, token)
    await communicator.send_input({"type": "websocket.connect"})
    while True:
        try:
            message = await communicator.receive_output(timeout=5)
        except Exception:
            break
        if message["type"] == "websocket.close":
            break
        if leave_after_ready and message["type"] == "websocket.send":
            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1001}
            )
            break
    # A server that already closed has nothing left to wait for; what is
    # measured is what it kept, not how it said goodbye.
    with contextlib.suppress(Exception):
        await communicator.wait(timeout=5)


@pytest.mark.parametrize("outcome", ["no_token", "bad_token", "no_access", "accepted"])
async def test_changes_socket_handshakes_retain_nothing(
    outcome: str,
    pod_api: DatastoreApi,
    fixed_test_user,
    test_app,
) -> None:
    """A browser retrying the changes socket every few seconds for hours was
    the first suspect for the API's growth. Measured, it was not; this keeps
    it that way for every way a handshake can end."""
    token = fixed_test_user["token"]
    pod_id, token, leave = {
        "no_token": (pod_api.pod_id, "", False),
        "bad_token": (pod_api.pod_id, token[:-6] + "AAAAAA", False),
        "no_access": (str(uuid.uuid4()), token, False),
        "accepted": (pod_api.pod_id, token, True),
    }[outcome]
    for _ in range(20):
        await _handshake(test_app, pod_id, token, leave_after_ready=leave)
    await asyncio.sleep(0.2)

    tracemalloc.start(1)
    try:
        before = tracemalloc.take_snapshot()
        for _ in range(200):
            await _handshake(test_app, pod_id, token, leave_after_ready=leave)
        await asyncio.sleep(0.5)
        retained = _retained_bytes(before)
    finally:
        tracemalloc.stop()

    assert retained < 1 * _MIB, (
        f"{outcome} handshakes retained {retained / _MIB:.2f} MiB"
    )

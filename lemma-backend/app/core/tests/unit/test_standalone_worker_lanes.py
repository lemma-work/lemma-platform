"""The embedded app runs every lane, because it is the whole deployment.

`uvicorn local_app:app` is not one process among several. It is the entire Lemma
backend on desktop, in the Docker local stack, and in `make dev` — nothing else
consumes a queue in any of them.

It used to embed exactly one streaq Worker, the interactive one. Bulk work is a
separate Worker on a separate Redis queue, so nothing anywhere consumed
`default-bulk`: every uploaded file sat at PENDING with `processing_attempts` 0
forever, `pod_search_files` returned an empty list that was indistinguishable
from "no matches", and `pod_read_file(format="markdown")` 404ed. The two crons
that would have recovered it are on the same lane, so nothing noticed.

`test_worker_lanes.py` covers `run_worker_lanes` itself and passed throughout —
it tests the entrypoint cloud runs. These cover the one local and desktop run.
"""

from __future__ import annotations

import signal

import anyio
import pytest
from anyio import TASK_STATUS_IGNORED, sleep_forever
from fastapi import FastAPI

from app.core.infrastructure.jobs import streaq_runtime
from app.core.infrastructure.jobs.streaq_runtime import (
    LANE_WORKERS,
    Lane,
    lane_for_task,
    lane_queue_name,
)
from app.core.infrastructure.jobs.task_dump import install_task_dump_handler
from app.standalone import build_standalone_app


class _FakeNoCronRedis:
    """A Redis that answers "no crons", which is what a clean queue looks like."""

    async def zrange(self, *_args, **_kwargs):
        return ()

    async def hkeys(self, *_args, **_kwargs):
        return ()


class _FakeLaneWorker:
    """Stands in for a streaq Worker without touching Redis.

    The cron keys and registry are part of that shape, not decoration: starting
    a lane sweeps cron schedules whose function no longer exists, and a double
    that lacks them would have this suite certify a startup path that cannot
    run.
    """

    def __init__(self, queue_name: str) -> None:
        self.queue_name = queue_name
        self.handle_signals = True
        self.signal_handler = None
        self.registry: dict[str, object] = {}
        self.redis = _FakeNoCronRedis()
        self.cron_schedule_key = f"streaq:{queue_name}:cron:schedule"
        self.cron_registry_key = f"streaq:{queue_name}:cron:jobs"
        self.cron_data_key = f"streaq:{queue_name}:cron:data:"

    async def run_async(self, *, task_status=TASK_STATUS_IGNORED) -> None:
        _CONSUMED.append(self.queue_name)
        if len(_CONSUMED) == len(list(Lane)):
            _ALL_STARTED.set()
        task_status.started()
        await sleep_forever()


_CONSUMED: list[str] = []
_ALL_STARTED = anyio.Event()


@pytest.mark.asyncio
async def test_the_embedded_app_consumes_every_lane_queue(monkeypatch):
    """Keyed on the Redis queue name, because that is what "consumes" means.

    Asserting on lane *enum members* would have passed against the old code as
    easily as the new one; only the queue name distinguishes a process that
    reads `default-bulk` from one that does not.
    """
    global _ALL_STARTED
    _CONSUMED.clear()
    _ALL_STARTED = anyio.Event()

    fakes = {lane: _FakeLaneWorker(lane_queue_name(lane)) for lane in Lane}
    monkeypatch.setattr(streaq_runtime, "LANE_WORKERS", fakes)

    app = build_standalone_app(FastAPI(), fakes[Lane.INTERACTIVE])
    async with app.router.lifespan_context(app):
        with anyio.fail_after(5):
            await _ALL_STARTED.wait()

    assert set(_CONSUMED) == {lane_queue_name(lane) for lane in Lane}
    # Named explicitly: this is the queue that was going unread.
    assert lane_queue_name(Lane.BULK) in _CONSUMED


def test_document_processing_lands_on_a_lane_the_embedded_app_runs():
    """Closes the loop from the queue back to the work that goes on it.

    The test above would still pass if document processing were moved to a lane
    nobody embeds. This one names the task, so that move fails here instead of
    in a user's pod six weeks later.
    """
    import app.events  # noqa: F401 — registers every task on its lane

    lane = lane_for_task("process_datastore_file_task")
    assert lane in list(Lane)
    assert "process_datastore_file_task" in LANE_WORKERS[lane].registry


def test_the_embedded_app_silences_signals_on_every_lane(monkeypatch):
    """uvicorn owns process signals; a lane that installs its own fights it.

    Previously only the single embedded worker was silenced, which was correct
    while only one ran. Now that every lane runs here, each has to be.
    """
    fakes = {lane: _FakeLaneWorker(lane_queue_name(lane)) for lane in Lane}
    monkeypatch.setattr(streaq_runtime, "LANE_WORKERS", fakes)

    build_standalone_app(FastAPI(), fakes[Lane.INTERACTIVE])
    # Preparation happens inside the lifespan, so run it.
    from app.standalone import _prepare_embedded_worker

    _prepare_embedded_worker(fakes[Lane.INTERACTIVE])
    for lane_worker in fakes.values():
        assert lane_worker.handle_signals is False
        assert lane_worker.signal_handler is not None


@pytest.mark.asyncio
async def test_task_dump_handler_survives_a_platform_without_sigquit(monkeypatch):
    """Windows has no SIGQUIT, and `run_worker_lanes` installs this first.

    Harmless while only `python -m app.worker` reached it, since Desktop never
    ran that. The moment the embedded app runs its lanes, this is the first
    thing a Windows backend executes — and `signal.SIGQUIT` raises
    AttributeError, which the platform guard did not catch.

    Async on purpose. `asyncio.get_running_loop()` is evaluated before the
    SIGQUIT lookup, so without a running loop this passes on a RuntimeError it
    was never meant to test — which is exactly how it passed against the
    unguarded code the first time it was written.
    """
    monkeypatch.delattr(signal, "SIGQUIT", raising=False)
    install_task_dump_handler()  # must not raise

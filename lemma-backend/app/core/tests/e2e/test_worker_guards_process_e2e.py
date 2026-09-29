"""The stream guard and the lane watchdog, in a real worker process.

Everything else pins the pieces: the budget arithmetic, the gap records, the
watchdog's reading of task state. This runs ``python -m app.worker`` itself --
the entrypoint, the lifespan that starts the guard and the watchdog, the exit
status the entrypoint derives -- against a real Redis and PostgreSQL, and
checks the two things a deployment actually depends on:

* a stream behind a consumer group that never reads is held to the budget,
  and the loss is recorded for replay instead of growing until Redis refuses
  every write, as ``datastore.events`` did in development on 2026-09-28;
* an ordinary SIGTERM still ends the worker with status 0. The watchdog must
  never read an orderly shutdown as a lane dying and report it as status 70.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio

from app.core.infrastructure.events.gap_replay import gap_key
from app.core.infrastructure.jobs.lane_watchdog import CHECK_INTERVAL_SECONDS
from app.modules.datastore.config import datastore_settings
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.worker]

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager

#: Declared (it has a MAXLEN override, so the guard watches it) and read by no
#: subscriber in the worker -- so a group added here is genuinely never read.
_STREAM = "usage_events"
_DEAD_GROUP = "e2e-guard-dead"
_BUDGET_BYTES = 2_000_000
_ENTRY = "x" * 4_000


#: A Redis logical database of this test's own. The worker it starts trims to a
#: tiny budget every second, and in the shared per-xdist-worker database that
#: would trim -- and later replay -- other tests' streams. Per-worker databases
#: start at 0 and stay far below this at the suite's parallelism.
_REDIS_DB = 15


def _isolated_redis_url(url: str) -> str:
    base, _, tail = url.rpartition("/")
    return f"{base}/{_REDIS_DB}" if tail.isdigit() else f"{url.rstrip('/')}/{_REDIS_DB}"


@pytest_asyncio.fixture
async def redis(e2e_settings):
    client = redis_asyncio.from_url(
        _isolated_redis_url(e2e_settings.redis_url), decode_responses=True
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def worker_process(request, e2e_settings, db_manager, redis):
    """A real worker with a short guard interval and a small stream budget.

    ``indirect`` parametrisation overrides its environment.
    """
    del db_manager  # schema only
    overrides: dict[str, str] = getattr(request, "param", {})
    queue_name = f"guard-test-{uuid4().hex[:8]}"
    log_path = Path(f"/tmp/lemma_guard_worker_{uuid4().hex}.log")
    backend_root = Path(__file__).resolve().parents[4]
    log_file = open(log_path, "w+")
    proc = subprocess.Popen(
        [str(backend_root / ".venv/bin/python"), "-m", "app.worker"],
        cwd=str(backend_root),
        env={
            **os.environ,
            "PYTHONPATH": ".",
            "PYTHONUNBUFFERED": "1",
            # A queue of its own, so it never takes the shared worker's jobs.
            "WORKER_QUEUE_NAME": queue_name,
            "DATABASE_URL": e2e_settings.database_url,
            "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url,
            "REDIS_URL": _isolated_redis_url(e2e_settings.redis_url),
            "SUPERTOKENS_CORE_URL": e2e_settings.supertokens_core_url,
            "ENVIRONMENT": "testing",
            "EMAIL_TRANSPORT": "filesystem",
            "STORAGE_BACKEND": "local",
            "EMBEDDING_PROVIDER": "local",
            "REDIS_STREAM_GUARD_INTERVAL_SECONDS": "1",
            "REDIS_STREAMS_BUDGET_BYTES": str(_BUDGET_BYTES),
            # This test is about the budget, not about restarting over a group.
            "REDIS_STREAM_STALL_SECONDS": "0",
            **overrides,
        },
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def logs() -> str:
        log_file.flush()
        log_file.seek(0)
        return log_file.read()

    async def probe() -> dict:
        return {"logs": logs(), "code": proc.poll()}

    try:
        await eventually(
            label="worker startup",
            probe=probe,
            done=lambda v: '"event": "service.started"' in v["logs"],
            fail_fast=lambda v: (
                f"worker exited before startup ({v['code']}):\n{v['logs'][-4000:]}"
                if v["code"] is not None
                else None
            ),
            timeout_seconds=60.0,
            interval_seconds=0.2,
        )

        # `service.started` is logged inside the lifespan, before streaq starts
        # its SIGTERM handler; a signal in that gap kills the process outright.
        # streaq starts the handler just before the loop that writes this
        # health key, so the key existing means the handler is in place.
        async def health_keys() -> list[str]:
            return await redis.keys(f"streaq:{queue_name}:health:*")

        await eventually(
            label="the worker to install its signal handler",
            probe=health_keys,
            done=bool,
            timeout_seconds=30.0,
            interval_seconds=0.1,
        )
        yield proc, logs
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        log_file.close()
        log_path.unlink(missing_ok=True)


async def test_a_real_worker_holds_the_budget_and_stops_cleanly(worker_process, redis):
    proc, logs = worker_process
    await redis.xgroup_create(_STREAM, _DEAD_GROUP, id="0", mkstream=True)
    pipe = redis.pipeline()
    for index in range(1_500):  # about 6MB, three times the budget
        pipe.xadd(_STREAM, {"__data__": json.dumps({"n": index, "pad": _ENTRY})})
    await pipe.execute()

    async def stream_state() -> dict:
        return {
            "memory": int(await redis.memory_usage(_STREAM) or 0),
            "gap": await redis.exists(gap_key(_STREAM, _DEAD_GROUP)),
        }

    await eventually(
        label="the guard trimming through a group that never reads",
        probe=stream_state,
        done=lambda v: v["memory"] <= _BUDGET_BYTES and v["gap"],
        timeout_seconds=30.0,
        interval_seconds=0.5,
    )
    assert '"event": "redis.stream.unread_trimmed"' in logs()

    proc.terminate()
    assert proc.wait(timeout=60) == 0, logs()[-4000:]
    assert '"event": "worker.lane.dead"' not in logs()


@pytest.mark.parametrize(
    "worker_process",
    [{"REDIS_STREAM_GUARD_INTERVAL_SECONDS": "0"}],
    indirect=True,
)
async def test_switching_the_guard_off_leaves_the_worker_running(worker_process):
    """The documented emergency switch. The guard is watched like a lane, so a
    disabled guard that simply returned used to read as a dead one -- and the
    worker restarted itself, over and over, the moment it was switched off."""
    proc, logs = worker_process
    started = time.monotonic()

    async def still_running() -> dict:
        return {"code": proc.poll(), "elapsed": time.monotonic() - started}

    # Past a full watchdog pass, not just the first probe: the old failure came
    # from a done-callback, which is immediate, but the periodic check is the
    # other way a disabled guard could be misread.
    await eventually(
        label="the worker to stay up with the guard off",
        probe=still_running,
        done=lambda v: v["elapsed"] > CHECK_INTERVAL_SECONDS + 2,
        fail_fast=lambda v: (
            f"worker exited with {v['code']}:\n{logs()[-4000:]}"
            if v["code"] is not None
            else None
        ),
        timeout_seconds=CHECK_INTERVAL_SECONDS + 20,
        interval_seconds=0.5,
    )

    proc.terminate()
    assert proc.wait(timeout=60) == 0, logs()[-4000:]
    assert '"event": "worker.lane.dead"' not in logs()

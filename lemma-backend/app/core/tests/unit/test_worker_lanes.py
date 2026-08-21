"""Worker lane routing.

Lanes exist so a burst of bulk work (document ingestion, pod imports) cannot
occupy the worker slots that latency-sensitive work needs. That guarantee rests
on four things, each covered here: lanes are genuinely separate Redis queues,
every task is registered on exactly one lane, a process only runs the lanes it
was configured for, and a process that merely *publishes* routes to the same
lanes the worker consumes from.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.infrastructure.jobs import streaq_runtime
from app.core.infrastructure.jobs.streaq_runtime import (
    LANE_WORKERS,
    TASK_LANES,
    Lane,
    enabled_lanes,
    lane_concurrency,
    lane_for_task,
    lane_queue_name,
    run_worker_lanes,
)


def test_lanes_are_separate_redis_queues():
    """Same queue would defeat the whole point — one backlog, one budget."""
    names = {lane: lane_queue_name(lane) for lane in Lane}
    assert len(set(names.values())) == len(Lane), names


def test_interactive_lane_keeps_the_bare_queue_name(monkeypatch):
    """Existing queues, dashboards and in-flight jobs must survive the upgrade.

    Renaming the interactive queue would strand every job already enqueued on it
    at deploy time.
    """
    monkeypatch.setattr(streaq_runtime.settings, "worker_queue_name", "default")
    assert lane_queue_name(Lane.INTERACTIVE) == "default"
    assert lane_queue_name(Lane.BULK) == "default-bulk"


def test_each_lane_has_its_own_worker_and_concurrency_budget():
    assert set(LANE_WORKERS) == set(Lane)
    assert len({id(w) for w in LANE_WORKERS.values()}) == len(Lane)
    for lane in Lane:
        assert LANE_WORKERS[lane].concurrency == lane_concurrency(lane)


def test_bulk_concurrency_is_independent_of_interactive(monkeypatch):
    monkeypatch.setattr(streaq_runtime.settings, "worker_concurrency", 20)
    monkeypatch.setattr(streaq_runtime.settings, "worker_bulk_concurrency", 3)
    assert lane_concurrency(Lane.INTERACTIVE) == 20
    assert lane_concurrency(Lane.BULK) == 3


def test_unregistered_task_defaults_to_interactive():
    """Forgetting to annotate must degrade to pre-lane behaviour, not to a
    silently unconsumed queue."""
    assert "definitely-not-a-registered-task" not in TASK_LANES
    assert lane_for_task("definitely-not-a-registered-task") is Lane.INTERACTIVE


def test_every_task_is_registered_on_exactly_one_lane():
    """A task registered on two Workers would be consumed twice — and a cron
    registered on two lanes would fire once per lane on every tick."""
    import app.events  # noqa: F401 — populates the registry

    for lane in Lane:
        registered = set(LANE_WORKERS[lane].registry)
        for other in Lane:
            if other is lane:
                continue
            overlap = registered & set(LANE_WORKERS[other].registry)
            assert not overlap, f"{lane}/{other} both register: {sorted(overlap)}"


def test_a_publisher_only_process_still_routes_to_the_bulk_lane():
    """The API enqueues bulk work but never imports the handlers that declare it.

    ``TASK_LANES`` is filled by the ``@streaq_task`` decorators, i.e. as a side
    effect of importing each module's handlers — which the worker does via
    ``app.events`` and the API does not. So the API's table was empty, every
    bulk task it enqueued was routed to the *interactive* queue, and the
    interactive worker dropped each one as "missing function". Pod bundle
    export, import and GitHub publish are enqueued only from the API, so all
    three silently did nothing: the export sat at QUEUED until it expired.

    This has to be a subprocess. The property under test is what a process that
    never imported ``app.events`` sees, and by the time this suite runs, this
    one has.
    """
    backend_root = Path(__file__).resolve().parents[4]
    script = """
import app.app  # the API process: controllers, no handlers
from app.core.infrastructure.jobs.streaq_runtime import Lane, lane_for_task

bulk = (
    "export_pod_bundle",
    "plan_pod_import",
    "apply_pod_import",
    "import_pod_url",
    "import_pod_github",
    "publish_pod_github",
    "process_datastore_file_task",
)
wrong = [name for name in bulk if lane_for_task(name) is not Lane.BULK]
print("MISROUTED:" + ",".join(wrong))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    reported = [
        line for line in result.stdout.splitlines() if line.startswith("MISROUTED:")
    ]
    assert reported, f"probe did not report: {result.stdout[-2000:]}"
    assert reported[-1] == "MISROUTED:", reported[-1]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", [Lane.INTERACTIVE, Lane.BULK]),
        ("   ", [Lane.INTERACTIVE, Lane.BULK]),
        ("interactive", [Lane.INTERACTIVE]),
        ("interactive,bulk", [Lane.INTERACTIVE, Lane.BULK]),
        ("BULK, Interactive", [Lane.INTERACTIVE, Lane.BULK]),
        ("bulk,bulk", [Lane.BULK]),
    ],
)
def test_enabled_lanes_parsing(monkeypatch, raw, expected):
    monkeypatch.setattr(streaq_runtime.settings, "worker_lanes", raw)
    assert enabled_lanes() == expected


def test_default_runs_every_lane(monkeypatch):
    """Single-process deployments — local stack, desktop, today's cloud worker —
    must keep working with no new configuration."""
    monkeypatch.setattr(streaq_runtime.settings, "worker_lanes", "")
    assert set(enabled_lanes()) == set(Lane)


def test_primary_lane_is_ordered_first(monkeypatch):
    """It owns the shared lifespan, so it has to start before the others."""
    monkeypatch.setattr(streaq_runtime.settings, "worker_lanes", "bulk,interactive")
    assert enabled_lanes()[0] is Lane.INTERACTIVE


def test_unknown_lane_name_fails_loudly(monkeypatch):
    """A typo must not silently drop a lane and leave its queue unconsumed."""
    monkeypatch.setattr(streaq_runtime.settings, "worker_lanes", "interactive,quick")
    with pytest.raises(ValueError, match="quick"):
        enabled_lanes()


@pytest.mark.asyncio
async def test_running_without_the_primary_lane_is_rejected():
    """Nothing else starts the broker, engine, or watchdog."""
    with pytest.raises(ValueError, match="interactive"):
        await run_worker_lanes([Lane.BULK])


# --- Observability middleware wiring --------------------------------------
#
# Regression: the per-lane refactor initially registered one shared middleware
# function across both workers and discarded what Worker.middleware() returned.
# streaq exposes the running task on THAT returned object, not on the function
# passed in, so every task raised AttributeError at run time. Unit tests missed
# it because none of them execute a task through the middleware; this closes
# that gap without needing a live worker.


def test_every_lane_has_an_observability_middleware_registered():
    for lane, worker in LANE_WORKERS.items():
        assert worker.middlewares, f"{lane} has no middleware registered"


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", list(Lane))
async def test_lane_middleware_actually_runs_a_task(lane, monkeypatch):
    """Execute the middleware for real — the only way to catch this class of bug.

    A structural assertion would not have caught the original defect: the
    registered object was a perfectly valid RegisteredMiddleware; what was broken
    was the closure INSIDE it reaching for `.context` on a plain function. Only
    invoking the wrapper surfaces that.
    """
    from streaq.task import _task_context

    worker = LANE_WORKERS[lane]
    assert worker.middlewares, f"{lane} has no middleware registered"

    # Redis and the job-context sidecar are irrelevant here; stub both so this
    # stays a pure unit test of the wiring. `worker.redis` needs stubbing too
    # because it is evaluated as an argument before the lookup is even called,
    # and an uninitialized Worker raises on that attribute.
    async def _no_inherited_context(_redis, _job_id):
        return {}

    monkeypatch.setattr(
        streaq_runtime, "load_job_observability_context", _no_inherited_context
    )
    monkeypatch.setattr(type(worker), "redis", property(lambda _self: None))

    calls: list[str] = []

    async def target(*_args, **_kwargs):
        calls.append("ran")
        return "result"

    task_context = SimpleNamespace(
        task_id="job-1", fn_name="some_task", tries=1, timeout=None
    )
    token = _task_context.set(task_context)
    try:
        wrapped = target
        for registered in worker.middlewares:
            wrapped = registered(wrapped)
        assert await wrapped() == "result"
    finally:
        _task_context.reset(token)

    assert calls == ["ran"]


def test_lane_middlewares_are_distinct_objects():
    """Sharing one registration across lanes is what caused the original bug."""
    seen = [id(m) for worker in LANE_WORKERS.values() for m in worker.middlewares]
    assert len(seen) == len(set(seen))


def test_only_the_primary_lane_watches_for_signals():
    """Two signal handlers in one process means SIGTERM never fully lands.

    streaq's handler cancels only its OWN worker's scope, so a handler per lane
    stops one lane and leaves the others running — the process then hangs until
    it is SIGKILLed, and an in-flight agent run never finalizes. The primary
    handles the signal; run_worker_lanes stops the rest when it unwinds.
    """
    handlers = {lane: worker.handle_signals for lane, worker in LANE_WORKERS.items()}
    assert handlers[Lane.INTERACTIVE] is True
    assert [lane for lane, on in handlers.items() if on] == [Lane.INTERACTIVE]

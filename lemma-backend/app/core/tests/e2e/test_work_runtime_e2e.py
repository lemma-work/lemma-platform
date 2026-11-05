"""The assembled runtime: a NOTIFY wakes the right dispatcher well before its poll."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.infrastructure.work import runtime as runtime_module
from app.core.infrastructure.work.context import FastContext
from app.core.infrastructure.work.registry import WorkRegistry
from app.core.infrastructure.work.runtime import WorkRuntime
from app.core.infrastructure.work.types import WorkClass, WorkMode
from app.core.tests.e2e.work_support import (
    POD_A,
    Demo,
    WorkDb,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def test_a_notify_wakes_an_idle_dispatcher_long_before_its_poll(
    work_db: WorkDb, registry: WorkRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime_module.work_settings, "work_poll_interval_seconds", 30.0
    )
    done = asyncio.Event()

    @registry.handler(
        kind="demo.fast", cls=WorkClass.EVENTS, mode=WorkMode.FAST, payload=Demo
    )
    async def handle(ctx: FastContext, payload: Demo) -> None:
        done.set()

    url = work_db.engine.url.render_as_string(hide_password=False)
    runtime = WorkRuntime(
        work_db.sessions,
        classes=[WorkClass.EVENTS],
        registry=registry,
        database_url=url,
    )
    await runtime.start()
    try:
        # Past the listener's connect-time wake, so only a NOTIFY can wake it.
        await asyncio.sleep(1.0)
        started = time.monotonic()
        await work_db.enqueue(registry, "demo.fast", POD_A)
        await asyncio.wait_for(done.wait(), timeout=5)
        elapsed = time.monotonic() - started
    finally:
        await runtime.stop(grace_seconds=1)
    print(f"notify-to-handler latency: {elapsed * 1000:.1f} ms")
    assert elapsed < 2.0
    assert await work_db.count() == 0


async def test_a_runtime_with_no_classes_does_nothing(work_db: WorkDb) -> None:
    runtime = WorkRuntime(work_db.sessions, classes=[], registry=WorkRegistry())
    await runtime.start()
    await runtime.stop()
    assert runtime.dispatchers == {}

"""Every startup step says how long it took, including the one that failed.

A boot that spent ~45s silently in the lifespan could not say which step it
was. The failing step matters most: it is the one a crash-looping pod needs
named.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.observability import startup_timing
from app.core.observability.startup_timing import (
    finish_startup,
    release_startup_heap,
    startup_step,
)


def _events(caplog, name: str) -> list[dict]:
    """structlog hands the whole event to the stdlib record as a dict."""
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict) and record.msg.get("event") == name
    ]


def _steps(caplog) -> list[dict]:
    return _events(caplog, "service.startup.step")


async def test_a_step_logs_its_duration(caplog) -> None:
    caplog.set_level(logging.INFO)
    async with startup_step("channel_connect", service="lemma-test"):
        pass

    [entry] = _steps(caplog)
    assert entry["step"] == "channel_connect"
    assert entry["ok"] is True
    assert entry["duration_ms"] >= 0


async def test_a_failing_step_is_still_logged_and_still_raises(caplog) -> None:
    caplog.set_level(logging.INFO)

    async def connect() -> None:
        raise RuntimeError("redis is down")

    with pytest.raises(RuntimeError):
        async with startup_step("message_bus_connect", service="lemma-test"):
            await connect()

    [entry] = _steps(caplog)
    assert entry["step"] == "message_bus_connect"
    assert entry["ok"] is False


async def test_a_step_over_budget_is_reported_as_degraded(caplog, monkeypatch) -> None:
    """The info line says how long; this one says it should not be here.

    A backfill over every pod schema grew into most of API startup, one info
    line among dozens. The budget is lowered rather than a real second slept.
    """
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(startup_timing, "SLOW_STEP_MS", 0.0)

    async with startup_step("datastore._backfill", service="lemma-test"):
        await asyncio.sleep(0.001)

    [entry] = _events(caplog, "service.startup.slow_step.degraded")
    assert entry["level"] == "warning"
    assert entry["step"] == "datastore._backfill"
    assert entry["duration_ms"] > 0


async def test_a_step_within_budget_is_not_degraded(caplog) -> None:
    caplog.set_level(logging.INFO)
    async with startup_step("channel_connect", service="lemma-test"):
        pass

    assert _events(caplog, "service.startup.slow_step.degraded") == []


def test_startup_freezes_the_heap_and_a_lifespan_end_releases_it() -> None:
    import gc
    import time

    try:
        startup_ms, frozen = finish_startup(time.monotonic())
        assert frozen > 0
        assert startup_ms >= 0
    finally:
        release_startup_heap()
    assert gc.get_freeze_count() == 0


def test_an_embedded_worker_ending_first_does_not_unfreeze_the_api() -> None:
    import gc
    import time

    finish_startup(time.monotonic())  # API
    finish_startup(time.monotonic())  # embedded worker
    try:
        release_startup_heap()  # worker ends first
        assert gc.get_freeze_count() > 0
    finally:
        release_startup_heap()  # API ends
    assert gc.get_freeze_count() == 0

"""Every startup step says how long it took, including the one that failed.

A boot that spent ~45s silently in the lifespan could not say which step it
was. The failing step matters most: it is the one a crash-looping pod needs
named.
"""

from __future__ import annotations

import logging

import pytest

from app.core.observability.startup_timing import freeze_startup_heap, startup_step


def _steps(caplog) -> list[dict]:
    """structlog hands the whole event to the stdlib record as a dict."""
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "service.startup.step"
    ]


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


def test_freezing_moves_live_objects_out_of_the_collector() -> None:
    import gc

    try:
        assert freeze_startup_heap() > 0
        assert gc.get_freeze_count() > 0
    finally:
        gc.unfreeze()

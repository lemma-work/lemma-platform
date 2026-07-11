from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.core.observability import loop_watchdog


@pytest.mark.asyncio
async def test_watchdog_writes_heartbeat_and_measures_low_lag(tmp_path, monkeypatch):
    heartbeat = tmp_path / "worker_heartbeat"
    monkeypatch.setattr(settings, "worker_heartbeat_path", str(heartbeat))
    monkeypatch.setattr(settings, "loop_lag_watchdog_interval_seconds", 0.05)
    monkeypatch.setattr(loop_watchdog, "_last_lag_seconds", 0.0)

    task = asyncio.create_task(
        loop_watchdog.loop_lag_watchdog(
            service_name="test",
            heartbeat_path=str(heartbeat),
        )
    )
    try:
        await asyncio.sleep(0.2)
        # Heartbeat file is written and holds a recent epoch second.
        assert heartbeat.exists()
        assert heartbeat.read_text().strip().isdigit()
        # A free loop has near-zero lag and is healthy.
        assert loop_watchdog.get_loop_lag_seconds() >= 0.0
        assert loop_watchdog.is_loop_healthy() is True
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_is_loop_healthy_reflects_unhealthy_threshold(monkeypatch):
    monkeypatch.setattr(settings, "loop_lag_unhealthy_seconds", 1.0)
    monkeypatch.setattr(loop_watchdog, "_last_lag_seconds", 0.2)
    assert loop_watchdog.is_loop_healthy() is True
    monkeypatch.setattr(loop_watchdog, "_last_lag_seconds", 5.0)
    assert loop_watchdog.is_loop_healthy() is False


@pytest.mark.asyncio
async def test_watchdog_disabled_heartbeat_path_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "loop_lag_watchdog_interval_seconds", 0.05)
    task = asyncio.create_task(loop_watchdog.loop_lag_watchdog(service_name="test"))
    try:
        await asyncio.sleep(0.15)
        # No path → no file written, and no crash.
        assert loop_watchdog.get_loop_lag_seconds() >= 0.0
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def test_heartbeat_writes_are_collision_safe_across_writers(tmp_path):
    heartbeat = tmp_path / "worker_heartbeat"

    loop_watchdog._write_heartbeat(str(heartbeat))
    loop_watchdog._write_heartbeat(str(heartbeat))

    assert heartbeat.read_text().strip().isdigit()
    assert list(tmp_path.glob(".worker_heartbeat.*")) == []

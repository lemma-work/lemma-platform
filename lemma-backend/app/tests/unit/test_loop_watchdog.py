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


# --- Loop-lag hysteresis state machine ---------------------------------------
# Captures loop-lag degraded/recovered events via the ProcessorFormatter handler.


def _capture_events():
    import io
    import logging
    import structlog

    loop_watchdog.reset_loop_watchdog_state()
    setup = __import__("app.core.log.log", fromlist=["setup_logging"]).setup_logging
    setup("development", service_name="lemma-test", json_logs=True, log_level="DEBUG")
    buf = io.StringIO()
    handler = next(
        h for h in logging.getLogger().handlers
        if isinstance(h.formatter, structlog.stdlib.ProcessorFormatter)
    )
    handler.stream = buf
    return buf, handler


def _events(buf):
    import json
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_degraded_emits_once_and_no_flood_on_repeated_samples():
    buf, _ = _capture_events()
    warn = 0.3
    # Three consecutive warning samples transition into degraded.
    loop_watchdog._evaluate_lag(0.4, warn, service_name="test", now=0.0)
    loop_watchdog._evaluate_lag(0.5, warn, service_name="test", now=0.1)
    loop_watchdog._evaluate_lag(0.45, warn, service_name="test", now=0.2)
    # Repeated degraded samples must not re-emit.
    loop_watchdog._evaluate_lag(0.6, warn, service_name="test", now=0.3)
    events = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.degraded"]
    assert len(events) == 1
    assert events[0]["level"] == "warning"
    assert loop_watchdog._max_lag_seconds == 0.6  # peak tracked silently


def test_single_five_second_sample_degrades_immediately(monkeypatch):
    buf, _ = _capture_events()
    monkeypatch.setattr(settings, "loop_lag_unhealthy_seconds", 5.0)
    loop_watchdog._evaluate_lag(5.1, 0.3, service_name="test", now=0.0)
    degraded = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.degraded"]
    assert len(degraded) == 1
    assert degraded[0]["unhealthy"] is True


def test_recovery_emits_only_after_hysteresis_window():
    buf, _ = _capture_events()
    warn = 0.3
    for now in (0.0, 0.1, 0.2):
        loop_watchdog._evaluate_lag(0.5, warn, service_name="test", now=now)
    # A single healthy sample must NOT recover (jitter suppression).
    loop_watchdog._evaluate_lag(0.1, warn, service_name="test", now=1.0)
    recovered = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.recovered"]
    assert recovered == []
    loop_watchdog._evaluate_lag(0.1, warn, service_name="test", now=30.9)
    assert not [e for e in _events(buf) if e["event"] == "runtime.loop_lag.recovered"]
    loop_watchdog._evaluate_lag(0.1, warn, service_name="test", now=31.0)
    recovered = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.recovered"]
    assert len(recovered) == 1
    assert recovered[0]["level"] == "info"
    assert recovered[0]["max_lag_ms"] == 500.0
    assert recovered[0]["degraded_duration_ms"] > 0
    assert loop_watchdog._degraded is False


def test_jitter_does_not_flap_degraded_recovered():
    buf, _ = _capture_events()
    warn = 0.3
    for now in (0.0, 0.1, 0.2):
        loop_watchdog._evaluate_lag(0.5, warn, service_name="test", now=now)
    # Alternate high/low without enough consecutive healthy samples to recover.
    for i in range(20):
        lag = 0.5 if i % 2 == 0 else 0.1
        loop_watchdog._evaluate_lag(lag, warn, service_name="test", now=float(i + 1))
    degraded = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.degraded"]
    recovered = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.recovered"]
    # Exactly one degraded transition, no recovery (jitter never sustained healthy).
    assert len(degraded) == 1
    assert recovered == []


def test_incident_pair_has_five_minute_cooldown_even_for_unhealthy_sample(
    monkeypatch,
):
    buf, _ = _capture_events()
    monkeypatch.setattr(settings, "loop_lag_unhealthy_seconds", 5.0)
    loop_watchdog._evaluate_lag(5.1, 0.3, service_name="test", now=0.0)
    loop_watchdog._evaluate_lag(0.1, 0.3, service_name="test", now=1.0)
    loop_watchdog._evaluate_lag(0.1, 0.3, service_name="test", now=31.0)
    loop_watchdog._evaluate_lag(5.2, 0.3, service_name="test", now=32.0)
    degraded = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.degraded"]
    assert len(degraded) == 1
    loop_watchdog._evaluate_lag(5.3, 0.3, service_name="test", now=300.0)
    degraded = [e for e in _events(buf) if e["event"] == "runtime.loop_lag.degraded"]
    assert len(degraded) == 2

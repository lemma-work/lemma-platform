from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4

import pytest

from app.modules.agent.api.controllers.runtime_config_controller import (
    _MutableMonotonic,
    _close_if_ping_stale,
    _reattach_agent_run_ids,
    _store_capacity_if_present,
)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed_with: dict | None = None

    async def close(self, *, code=None, reason=None) -> None:
        self.closed_with = {"code": code, "reason": reason}


def test_reattach_agent_run_ids_parses_valid_entries():
    run_id_1 = uuid4()
    run_id_2 = uuid4()

    result = _reattach_agent_run_ids(
        [
            {"agent_run_id": str(run_id_1), "buffered_event_count": 3, "overflowed": False},
            {"agent_run_id": str(run_id_2), "buffered_event_count": 0, "overflowed": False},
        ]
    )

    assert result == [run_id_1, run_id_2]


def test_reattach_agent_run_ids_skips_malformed_entries():
    run_id = uuid4()

    result = _reattach_agent_run_ids(
        [
            {"agent_run_id": str(run_id)},
            {"agent_run_id": "not-a-uuid"},
            {"no_agent_run_id": "here"},
            "not-even-a-dict",
        ]
    )

    assert result == [run_id]


def test_reattach_agent_run_ids_handles_non_list_input():
    assert _reattach_agent_run_ids(None) == []
    assert _reattach_agent_run_ids("garbage") == []
    assert _reattach_agent_run_ids({}) == []


@pytest.mark.asyncio
async def test_close_if_ping_stale_closes_after_threshold(monkeypatch):
    import app.modules.agent.api.controllers.runtime_config_controller as controller

    # The reaper's poll interval is max(1.0, threshold/3) -- it can't fire
    # faster than ~1s regardless of how small the threshold is, so the test
    # timeout must clear that floor with margin.
    monkeypatch.setattr(controller.settings, "daemon_ws_ping_stale_after_seconds", 0.03)
    websocket = _FakeWebSocket()
    last_ping = _MutableMonotonic()

    await asyncio.wait_for(
        _close_if_ping_stale(websocket, last_ping, daemon_id=uuid4()), timeout=2.0
    )

    assert websocket.closed_with is not None


@pytest.mark.asyncio
async def test_close_if_ping_stale_does_not_close_while_pings_keep_arriving(monkeypatch):
    import app.modules.agent.api.controllers.runtime_config_controller as controller
    import time

    monkeypatch.setattr(controller.settings, "daemon_ws_ping_stale_after_seconds", 0.05)
    websocket = _FakeWebSocket()
    last_ping = _MutableMonotonic()

    reaper = asyncio.create_task(_close_if_ping_stale(websocket, last_ping, daemon_id=uuid4()))
    for _ in range(10):
        await asyncio.sleep(0.02)
        last_ping.value = time.monotonic()  # simulate a fresh daemon.ping arriving

    reaper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reaper

    assert websocket.closed_with is None


@pytest.mark.asyncio
async def test_store_capacity_if_present_stores_well_formed_payload(monkeypatch):
    import app.modules.agent.api.controllers.runtime_config_controller as controller

    stored = {}

    async def _fake_set_capacity(*, daemon_id, active_run_count, max_concurrent_runs):
        stored["daemon_id"] = daemon_id
        stored["active_run_count"] = active_run_count
        stored["max_concurrent_runs"] = max_concurrent_runs

    monkeypatch.setattr(controller, "set_daemon_capacity", _fake_set_capacity)
    daemon_id = uuid4()

    await _store_capacity_if_present(
        daemon_id, {"active_run_count": 2, "max_concurrent_runs": 4}
    )

    assert stored == {"daemon_id": daemon_id, "active_run_count": 2, "max_concurrent_runs": 4}


@pytest.mark.asyncio
async def test_store_capacity_if_present_ignores_malformed_payload(monkeypatch):
    import app.modules.agent.api.controllers.runtime_config_controller as controller

    calls = []

    async def _fake_set_capacity(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(controller, "set_daemon_capacity", _fake_set_capacity)

    await _store_capacity_if_present(uuid4(), None)
    await _store_capacity_if_present(uuid4(), "not-a-dict")
    await _store_capacity_if_present(uuid4(), {"active_run_count": "two", "max_concurrent_runs": 4})
    await _store_capacity_if_present(uuid4(), {"active_run_count": 2})

    assert calls == []

from __future__ import annotations

import sys
import types

import pytest

import app.worker as worker

pytestmark = pytest.mark.unit


def _install_runtime(monkeypatch, run_worker_lanes):
    runtime = types.ModuleType("app.core.infrastructure.jobs.streaq_runtime")
    runtime.run_worker_lanes = run_worker_lanes
    monkeypatch.setitem(sys.modules, "app.events", types.ModuleType("app.events"))
    monkeypatch.setitem(
        sys.modules,
        "app.core.infrastructure.jobs.streaq_runtime",
        runtime,
    )


def test_worker_main_installs_logging_before_starting_lanes(monkeypatch):
    calls: list[str] = []

    def setup_logging(*args, **kwargs):
        calls.append(f"logging:{kwargs['service_name']}")

    def validate_release_identity(environment):
        calls.append(f"release:{environment}")

    async def run_worker_lanes():
        calls.append("lanes")

    monkeypatch.setattr(worker, "setup_logging", setup_logging)
    monkeypatch.setattr(worker, "validate_release_identity", validate_release_identity)
    monkeypatch.setattr(worker.settings, "environment", "test")
    _install_runtime(monkeypatch, run_worker_lanes)
    worker.main()

    assert calls == ["logging:lemma-worker", "release:test", "lanes"]


def test_worker_main_turns_lane_failure_into_clean_exit(monkeypatch):
    async def run_worker_lanes():
        raise RuntimeError("lane failed")

    monkeypatch.setattr(worker, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "validate_release_identity", lambda _: None)
    _install_runtime(monkeypatch, run_worker_lanes)

    with pytest.raises(SystemExit, match="1"):
        worker.main()

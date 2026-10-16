from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager

import pytest

import app.worker as worker

pytestmark = pytest.mark.unit


def test_worker_main_installs_logging_before_running_the_worker(monkeypatch):
    calls: list[str] = []

    def setup_logging(*args, **kwargs):
        calls.append(f"logging:{kwargs['service_name']}")

    def validate_release_identity(environment):
        calls.append(f"release:{environment}")

    async def run_worker(modules):
        calls.append(f"worker:{len(modules)}")

    monkeypatch.setattr(worker, "setup_logging", setup_logging)
    monkeypatch.setattr(worker, "validate_release_identity", validate_release_identity)
    monkeypatch.setattr(worker.settings, "environment", "test")
    monkeypatch.setattr(worker, "run_worker", run_worker)
    worker.main(modules=["a", "b"])  # type: ignore[list-item]

    assert calls == ["logging:lemma-worker", "release:test", "worker:2"]


def test_worker_main_turns_a_startup_failure_into_exit_1(monkeypatch):
    async def run_worker(modules):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(worker, "setup_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "validate_release_identity", lambda _: None)
    monkeypatch.setattr(worker, "run_worker", run_worker)

    with pytest.raises(SystemExit, match="1"):
        worker.main(modules=[])


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def begin_stop(self) -> None:
        self.calls.append("begin_stop")

    async def stop(self) -> None:
        self.calls.append("stop")


async def test_sigterm_starts_the_drain_at_once_and_waits_for_it(monkeypatch):
    """The signal begins the drain synchronously; ``run_worker`` returns after it."""
    from app.core.infrastructure.work import process

    runtime = _Runtime()

    @asynccontextmanager
    async def services(modules):
        yield runtime

    monkeypatch.setattr(process, "worker_services", services)
    task = asyncio.create_task(worker.run_worker([]))
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, timeout=5)
    assert runtime.calls == ["begin_stop", "stop"]

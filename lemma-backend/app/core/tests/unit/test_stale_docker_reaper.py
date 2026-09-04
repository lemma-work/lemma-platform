"""The stale-resource reaper must never touch anything still running.

This age-gated sweep replaces an unsafe pattern: an opt-in, presence-based
prune that, once enabled, removed EVERY matching container regardless of
whether it belonged to a concurrently running test session on the same Docker
daemon — a documented trap for anyone running two suites side by side on a
shared dev machine. Age-gating only fixes that if a running container is
unconditionally immune and the age threshold is actually enforced in both
directions. Both are asserted here directly against a faked ``docker``, so a
regression that silently loosens either guarantee fails loudly here rather
than showing up as a stomped concurrent session on someone else's machine.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

from app.core import test_utils


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_a_running_container_is_never_reaped_no_matter_its_age(monkeypatch) -> None:
    ancient = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return _completed("container-1\n")
        if command[:2] == ["docker", "inspect"]:
            return _completed(f"true|{ancient}")
        raise AssertionError(f"unexpected command, rm should never run: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    assert test_utils.reap_stale_lemma_containers() == []


def test_a_stopped_container_past_its_grace_period_is_reaped_once(
    monkeypatch,
) -> None:
    """Also covers dedup: the same id can match more than one label filter."""
    ancient = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    rm_calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return _completed("container-1\n")
        if command[:2] == ["docker", "inspect"]:
            return _completed(f"false|{ancient}")
        if command[:3] == ["docker", "rm", "-f"]:
            rm_calls.append(command)
            return _completed()
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    removed = test_utils.reap_stale_lemma_containers()

    assert removed == ["container-1"]
    assert rm_calls == [["docker", "rm", "-f", "-v", "container-1"]]


def test_a_recently_stopped_container_is_left_alone(monkeypatch) -> None:
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "ps"]:
            return _completed("container-1\n")
        if command[:2] == ["docker", "inspect"]:
            return _completed(f"false|{recent}")
        raise AssertionError(f"unexpected command, rm should never run: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    assert test_utils.reap_stale_lemma_containers() == []


def test_a_stale_named_sandbox_volume_is_reaped(monkeypatch) -> None:
    ancient = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()

    def fake_run(command, **kwargs):
        if command[:3] == ["docker", "volume", "ls"]:
            return _completed("lemma-vol-abc-1\n")
        if command[:3] == ["docker", "volume", "inspect"]:
            return _completed(ancient)
        if command[:3] == ["docker", "volume", "rm"]:
            return _completed()
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    assert test_utils.reap_stale_lemma_volumes() == ["lemma-vol-abc-1"]


def test_a_volume_still_attached_to_a_container_is_not_counted_as_removed(
    monkeypatch,
) -> None:
    """``docker volume rm`` fails (nonzero) on an attached volume — that must
    not be reported as removed, and must not raise."""
    ancient = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()

    def fake_run(command, **kwargs):
        if command[:3] == ["docker", "volume", "ls"]:
            return _completed("lemma-vol-abc-1\n")
        if command[:3] == ["docker", "volume", "inspect"]:
            return _completed(ancient)
        if command[:3] == ["docker", "volume", "rm"]:
            return _completed(returncode=1)
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    assert test_utils.reap_stale_lemma_volumes() == []


def test_a_recent_named_sandbox_volume_is_left_alone(monkeypatch) -> None:
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_run(command, **kwargs):
        if command[:3] == ["docker", "volume", "ls"]:
            return _completed("lemma-vol-abc-1\n")
        if command[:3] == ["docker", "volume", "inspect"]:
            return _completed(recent)
        raise AssertionError(f"unexpected command, rm should never run: {command}")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: True)
    monkeypatch.setattr(test_utils.subprocess, "run", fake_run)

    assert test_utils.reap_stale_lemma_volumes() == []


def test_without_docker_installed_both_reapers_are_no_ops(monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called without docker")

    monkeypatch.setattr(test_utils, "_has_docker", lambda: False)
    monkeypatch.setattr(test_utils.subprocess, "run", fail_run)

    assert test_utils.reap_stale_lemma_containers() == []
    assert test_utils.reap_stale_lemma_volumes() == []

"""Cross-platform process-lifecycle helpers for the daemon.

These guard the Windows-compatibility fixes: the liveness probe must not rely on
POSIX ``kill(pid, 0)`` semantics (signal 0 is ``CTRL_C_EVENT`` on Windows), and
the background daemon must detach via a new process group on Windows rather than
the POSIX-only ``start_new_session``.
"""
from __future__ import annotations

import os
import sys

import pytest

from lemma_cli.daemon import commands as daemon_commands
from lemma_cli.daemon import config as daemon_config


def test_process_is_running_true_for_current_process():
    assert daemon_config.process_is_running(os.getpid()) is True


def test_process_is_running_false_for_unused_pid():
    # A pid this large is effectively never allocated on POSIX or Windows.
    assert daemon_config.process_is_running(2_000_000_000) is False


@pytest.mark.parametrize("pid", [0, -1, -1234])
def test_process_is_running_false_for_nonpositive_pid(pid):
    assert daemon_config.process_is_running(pid) is False


def test_process_is_running_delegates_to_windows_helper(monkeypatch):
    seen = {}

    def fake_windows(pid: int) -> bool:
        seen["pid"] = pid
        return True

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(daemon_config, "_windows_process_is_running", fake_windows)
    # On the Windows path the POSIX kill(pid, 0) probe must never run.
    monkeypatch.setattr(
        daemon_config.os,
        "kill",
        lambda *args, **kwargs: pytest.fail("os.kill must not be used on win32"),
    )

    assert daemon_config.process_is_running(4321) is True
    assert seen["pid"] == 4321


def test_detach_kwargs_posix_uses_new_session(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert daemon_commands._detach_kwargs() == {"start_new_session": True}


def test_detach_kwargs_windows_uses_detached_process_group(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    # These flag constants only exist on Windows; provide them (using their real
    # values) so the branch is exercisable on any host running the suite.
    monkeypatch.setattr(
        daemon_commands.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False
    )
    monkeypatch.setattr(
        daemon_commands.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False
    )
    monkeypatch.setattr(
        daemon_commands.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )

    kwargs = daemon_commands._detach_kwargs()

    assert set(kwargs) == {"creationflags"}
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200 | 0x08000000
    assert "start_new_session" not in kwargs

"""A login copy has three outcomes, and a disk is never replaced on a failed one.

The shell commands are run for real in ``sh`` with the machine's paths
pointed at a temporary directory; the machine itself is a function that runs
them there.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services import login_backup
from app.modules.workspace.services.login_backup import (
    BackupOutcome,
    LoginBackupFailed,
    LoginBackups,
)
from app.modules.workspace.services.machine_lifecycle import MachineLifecycle
from app.modules.workspace.services.machine_shell import ShellOutcome
from sandbox_runtime.paths import BROWSER_PROFILE_ROOT


def _sh(command: str, *, profile: Path, stage: str) -> int:
    command = command.replace(BROWSER_PROFILE_ROOT, str(profile)).replace(
        "/tmp/lemma-login-backup.tgz", stage
    )
    return subprocess.run(["sh", "-c", command], timeout=30).returncode


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "browser"
    (profile / "profile" / "Default").mkdir(parents=True)
    (profile / "profile" / "Default" / "Cookies").write_text("session=1")
    return profile


def test_a_tar_that_fails_fails_the_command(tmp_path: Path) -> None:
    """`tar …; chmod` exited 0 for a failed tar, and a partial archive then
    displaced the good copies."""
    profile = _profile(tmp_path)

    status = _sh(
        login_backup._tar_command(),
        profile=profile,
        stage=str(tmp_path / "missing-dir" / "x.tgz"),
    )

    assert status not in (0, 3)


def test_a_good_tar_succeeds_and_nothing_to_copy_is_3(tmp_path: Path) -> None:
    stage = str(tmp_path / "x.tgz")
    assert (
        _sh(login_backup._tar_command(), profile=_profile(tmp_path), stage=stage) == 0
    )
    assert os.stat(stage).st_mode & 0o777 == 0o600

    empty = tmp_path / "empty"
    empty.mkdir()
    assert _sh(login_backup._tar_command(), profile=empty, stage=stage) == 3


def test_the_browser_close_waits_for_a_live_chrome_and_not_a_dead_one(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    lock = profile / "profile" / "SingletonLock"
    command = login_backup.close_browser_command(wait_seconds=1).replace(
        login_backup.sandbox_command("agent-browser"), "true"
    )

    lock.symlink_to(f"host-{os.getpid()}")
    assert _sh(command, profile=profile, stage="") == 4, "a live Chrome holds it"

    lock.unlink()
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock.symlink_to(f"host-{dead.pid}")
    assert _sh(command, profile=profile, stage="") == 0, "a stale lock is no Chrome"

    lock.unlink()
    assert _sh(command, profile=profile, stage="") == 0


def _shell(results: dict[str, ShellOutcome]):
    async def run(machine_id, *, acting_user, command, timeout_seconds):
        for marker, outcome in results.items():
            if marker in command:
                return outcome
        return ShellOutcome(0, "", "")

    return run


@pytest.mark.parametrize(
    ("exit_code", "outcome"),
    [(3, BackupOutcome.NOTHING), (2, BackupOutcome.FAILED)],
)
async def test_nothing_to_copy_and_a_failed_copy_are_told_apart(
    exit_code, outcome
) -> None:
    backups = LoginBackups(
        None,  # type: ignore[arg-type]
        shell=_shell({"tar czf": ShellOutcome(exit_code, "", "")}),
    )

    result = await backups.back_up(
        uuid4(), acting_user=uuid4(), reason="before_reset", force=True
    )

    assert result.outcome is outcome


class _UnverifiedStore(LoginBackups):
    """An object store whose copy does not read back intact."""

    async def _store_backup(self, sandbox_id, plain, *, reason):
        return None


async def test_a_copy_that_does_not_read_back_is_a_failure() -> None:
    async def staged(sandbox_id):
        return b"archive"

    backups = _UnverifiedStore(
        None,  # type: ignore[arg-type]
        shell=_shell({}),
        read_staged=staged,
    )

    result = await backups.back_up(
        uuid4(), acting_user=uuid4(), reason="before_reset", force=True
    )

    assert result.failed


async def test_a_browser_that_will_not_close_stops_the_copy() -> None:
    backups = LoginBackups(
        None,  # type: ignore[arg-type]
        shell=_shell({"close --all": ShellOutcome(4, "", "")}),
    )

    with pytest.raises(LoginBackupFailed):
        await backups.require_back_up(
            uuid4(), acting_user=uuid4(), reason="before_reset"
        )


class _FailingBackups:
    async def require_back_up(self, machine_id, *, acting_user, reason):
        raise LoginBackupFailed("the object store said no")


class _Service:
    def __init__(self) -> None:
        self.destroyed: list[object] = []

    async def destroy(self, machine_id, *, delete_storage):
        self.destroyed.append(machine_id)


async def test_a_home_reset_keeps_the_disk_when_the_logins_cannot_be_copied() -> None:
    service = _Service()
    lifecycle = MachineLifecycle(None)  # type: ignore[arg-type]
    lifecycle._backups = _FailingBackups()  # type: ignore[assignment]
    lifecycle._service = lambda: service  # type: ignore[method-assign]

    with pytest.raises(MachineConflict) as refused:
        await lifecycle.reset(
            SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
            ["home"],
            acting_user=uuid4(),
            confirm=True,
        )

    assert refused.value.code == "MACHINE_LOGIN_BACKUP_FAILED"
    assert service.destroyed == []

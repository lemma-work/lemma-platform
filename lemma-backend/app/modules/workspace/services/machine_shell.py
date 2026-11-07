"""Running one housekeeping command on a machine, on Lemma's own behalf.

For reset scopes, the disk janitor and the like: commands the backend sends,
not anybody's work. They run with no Lemma credentials in their environment --
nothing they do needs to call back -- so no token is minted for them.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from uuid import UUID

from sandbox_runtime.paths import HOME_ROOT


@dataclass(frozen=True, slots=True)
class ShellOutcome:
    exit_code: int | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


async def run_on_machine(
    machine_id: UUID,
    *,
    acting_user: UUID,
    command: str,
    timeout_seconds: int = 300,
) -> ShellOutcome:
    from app.modules.workspace.services.workspace_sandbox_service import (
        WorkspaceSandboxService,
    )

    service = WorkspaceSandboxService()
    try:
        session = await service.get_session(
            acting_user,
            pod_id=None,
            initial_cwd=HOME_ROOT,
            close_on_exit=True,
            env_vars={},
            machine_id=machine_id,
        )
        async with session:
            result = await session.execute_terminal_command(
                command, timeout=timeout_seconds
            )
    finally:
        await service.close()
    return ShellOutcome(
        exit_code=result.exit_code,
        stdout=result.stdout or "",
        stderr=result.stderr or "",
    )


#: Bytes used on the filesystem holding the home, as one integer.
DISK_USED = f"df --output=used -B1 {shlex.quote(HOME_ROOT)} | tail -1"
#: ``used total`` in bytes for the home's filesystem.
DISK_USAGE = f"df --output=used,size -B1 {shlex.quote(HOME_ROOT)} | tail -1"


def parse_disk_usage(stdout: str) -> tuple[int, int] | None:
    parts = stdout.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[-2]), int(parts[-1])
    except ValueError:
        return None

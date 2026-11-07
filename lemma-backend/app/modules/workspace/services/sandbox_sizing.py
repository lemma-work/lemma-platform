"""What size a sandbox should be built at.

A machine is built at the size recorded on its row: chosen when it was
created, within its plan's policy, and changed only by an explicit resize or
the move of default machines to 2x4. A row still at the legacy default is
built at whatever the deployment configured, which is what it has always been
built at. Everything else -- a function runtime -- keeps the configured size.

Read at every provision, so a resize reaches the machine the next time it is
built -- on Docker that is its next cold start, with the same disk; on E2B,
where the sandbox is the disk, an existing sandbox keeps the size it was
created at until its files are carried to a sandbox of the new size.
"""

from __future__ import annotations

from app.core.ports.plan_limits import SandboxSize
from app.modules.workspace.domain.machine import LEGACY_DEFAULT_SIZE, MachineSize
from app.modules.workspace.domain.sandbox import Sandbox, SandboxKind


def machine_size_for(sandbox: Sandbox) -> SandboxSize | None:
    """The size this machine is built at, or None for the configured default."""
    if sandbox.kind is not SandboxKind.WORKSPACE:
        return None
    try:
        size = MachineSize.parse(sandbox.size)
    except ValueError:
        return None
    if size == LEGACY_DEFAULT_SIZE:
        return None
    return size.sandbox_size

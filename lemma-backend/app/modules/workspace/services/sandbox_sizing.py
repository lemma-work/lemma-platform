"""What size a sandbox should be built at.

A person's workspace is sized by their plan; everything else keeps the size the
deployment configured. Asked at every provision, so a plan change reaches the
workspace the next time it is built -- on Docker that is its next cold start,
with the same disk; on E2B, where the sandbox is the disk, an existing
workspace keeps the size it was created at until its files can be carried to a
sandbox of the new size.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.plan_limits import build_plan_limits
from app.core.ports.plan_limits import SandboxSize
from app.modules.workspace.domain.sandbox import (
    Sandbox,
    SandboxKind,
    SandboxOwnerKind,
)


async def plan_size_for(
    uow: SqlAlchemyUnitOfWork, sandbox: Sandbox
) -> SandboxSize | None:
    """The plan's size for this sandbox, or None for the configured default."""
    if sandbox.kind is not SandboxKind.WORKSPACE:
        return None
    if sandbox.owner_kind is not SandboxOwnerKind.USER:
        return None
    plan_limits = build_plan_limits(uow)
    if plan_limits is None:
        return None
    return await plan_limits.workspace_size(user_id=sandbox.owner_id)

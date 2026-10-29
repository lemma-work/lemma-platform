"""What the sweep does for machines, beside reclaiming idle compute.

Each tick, in order:

1. meter every running machine's time, and notice the ones that just came
   back from a pause -- their services and Docker are started again;
2. enforce compute allowances (when enforcement is on);
3. release idle machines through the clean close: browser closed, logins
   copied off, then stop; a default machine still at the legacy size is moved
   to the default size instead, when that move is switched on;
4. take the daily copy of logins for machines that are up anyway.

Each step is best effort per machine: one machine that does not answer must
not stop the others being metered, released or backed up.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import (
    LEGACY_DEFAULT_SIZE,
    Machine,
    MachineKind,
    MachineTemplate,
)
from app.modules.workspace.domain.sandbox import Sandbox, SandboxKind
from app.modules.workspace.infrastructure.machine_repository import MachineRepository
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.services.login_backup import LoginBackups
from app.modules.workspace.services.machine_lifecycle import MachineLifecycle
from app.modules.workspace.services.machine_processes import MachineProcesses
from app.modules.workspace.services.machine_shell import run_on_machine
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)

#: What one machine's housekeeping can fail with, without failing the tick.
_PER_MACHINE_FAILURES: tuple[type[BaseException], ...] = (
    SandboxError,
    WorkspaceRuntimeError,
    OSError,
    ValueError,
)

#: Post-resume for Docker machines: a filesystem-only resume cold-boots the
#: VM and systemd brings dockerd back on its own, but not always quickly --
#: so wait for it, and start it only if it has not come up after ten seconds.
DOCKER_AFTER_RESUME = (
    "for i in $(seq 1 45); do docker info >/dev/null 2>&1 && exit 0; "
    '[ "$i" = 10 ] && sudo systemctl start docker; sleep 1; done; exit 1'
)


def _acting_user(machine: Machine) -> UUID:
    return machine.created_by or machine.owner_id


async def _machine(uow_factory: UnitOfWorkFactory, machine_id: UUID) -> Machine | None:
    async with uow_factory() as uow:
        return await MachineRepository(uow).get(machine_id)


async def after_resume(uow_factory: UnitOfWorkFactory, machine_ids: list[UUID]) -> None:
    """Services back up, Docker answering, and the logins back if the disk
    came up without them, on machines that just started or resumed."""
    processes = MachineProcesses()
    for machine_id in machine_ids:
        machine = await _machine(uow_factory, machine_id)
        if machine is None:
            continue
        try:
            if machine.template is MachineTemplate.WORKSPACE_DOCKER:
                await run_on_machine(
                    machine.id,
                    acting_user=_acting_user(machine),
                    command=DOCKER_AFTER_RESUME,
                    timeout_seconds=120,
                )
            restarted = await processes.restart_services(machine.id, base_env={})
            if await LoginBackups(uow_factory).restore_if_lost(
                machine.id, acting_user=_acting_user(machine)
            ):
                logger.info(
                    "workspace.machine_housekeeping.logins_restored.observed",
                    sandbox_id=str(machine_id),
                )
        except _PER_MACHINE_FAILURES as exc:
            logger.warning(
                "workspace.machine_housekeeping.resume_failed.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
            )
            continue
        if restarted:
            logger.info(
                "workspace.machine_housekeeping.services_restarted.observed",
                sandbox_id=str(machine_id),
                restarted_count=len(restarted),
            )


def machine_release(uow_factory: UnitOfWorkFactory):
    """The sweeper's release for an idle sandbox: the clean close for a
    machine, the plain release for anything else."""

    async def release(sandbox: Sandbox) -> None:
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        machine = (
            await _machine(uow_factory, sandbox.id)
            if sandbox.kind is SandboxKind.WORKSPACE
            else None
        )
        if machine is None:
            await get_sandbox_service().release(sandbox.id)
            return
        if await _move_size(uow_factory, machine):
            return
        await MachineLifecycle(uow_factory).release(
            machine, acting_user=_acting_user(machine)
        )

    return release


async def _move_size(uow_factory: UnitOfWorkFactory, machine: Machine) -> bool:
    if (
        not workspace_settings.machine_size_migration_enabled
        or machine.kind is not MachineKind.DEFAULT
        or machine.size != LEGACY_DEFAULT_SIZE
    ):
        return False
    from app.modules.workspace.services.machine_policy import policy_for_user
    from app.modules.workspace.services.size_migration import SizeMigration

    async with uow_factory() as uow:
        target = (await policy_for_user(uow, machine.owner_id)).default_size
    if target == machine.size:
        return False
    try:
        return await SizeMigration(uow_factory).migrate(
            machine, to=target, acting_user=_acting_user(machine)
        )
    except _PER_MACHINE_FAILURES as exc:
        logger.warning(
            "workspace.machine_housekeeping.size_move_failed.degraded",
            sandbox_id=str(machine.id),
            error_type=type(exc).__name__,
        )
        return False


async def slot_held(machine_id: UUID) -> bool:
    from app.modules.workspace.services.run_slots import RunSlots

    return bool(await RunSlots().holders(machine_id))


async def daily_login_copies(
    uow_factory: UnitOfWorkFactory, machine_ids: list[UUID]
) -> int:
    """The once-a-day copy, for machines that are running anyway. Never wakes one."""
    backups = LoginBackups(uow_factory)
    taken = 0
    for machine_id in machine_ids:
        machine = await _machine(uow_factory, machine_id)
        if machine is None or not await backups.due(machine_id, reason="daily"):
            continue
        try:
            if await backups.back_up(
                machine_id, acting_user=_acting_user(machine), reason="daily"
            ):
                taken += 1
        except _PER_MACHINE_FAILURES as exc:
            logger.warning(
                "workspace.machine_housekeeping.daily_copy_failed.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
            )
    return taken

"""What the sweep does for machines, beside reclaiming idle compute.

The sweep's steps, in the order `machine_sweep` runs them -- cost control
first, since a step that overruns its budget is cut off and the rest still
run:

1. meter every running machine's time, ending the sessions of machines that
   stopped without saying so (`compute_meter`);
2. release idle machines through the clean close: browser closed, logins
   copied off, then stop -- or, for a default machine still at the legacy
   size when that move is switched on, mark the move for its own job;
3. reclaim provider objects nothing owns any more;
4. bring back what a resume lost: services (with a freshly minted token),
   Docker, and the logins on a disk that came up without them;
5. enforce compute allowances (when enforcement is on);
6. take the daily copy of logins for machines that are up anyway.

Each step is best effort per machine, and each machine has its own budget:
one machine that does not answer must not stop the others being metered,
released or backed up.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import (
    LEGACY_DEFAULT_SIZE,
    Machine,
    MachineKind,
    MachineTemplate,
)
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.domain.sandbox import Sandbox, SandboxKind
from app.modules.workspace.infrastructure.machine_repository import MachineRepository
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from app.modules.workspace.services.login_backup import LoginBackupFailed, LoginBackups
from app.modules.workspace.services.machine_lifecycle import MachineLifecycle
from app.modules.workspace.services.machine_processes import MachineProcesses
from app.modules.workspace.services.machine_services import MachineServices
from app.modules.workspace.services.machine_shell import run_on_machine
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)

#: What one machine's housekeeping can fail with, without failing the tick:
#: the machine or its runtime not answering, a refusal, Redis or the database
#: blinking, or the machine's own budget running out.
PER_MACHINE_FAILURES: tuple[type[BaseException], ...] = (
    SandboxError,
    WorkspaceRuntimeError,
    MachineConflict,
    LoginBackupFailed,
    RedisError,
    SQLAlchemyError,
    OSError,
    ValueError,
    TimeoutError,
)

#: The most one machine may take in one step of the sweep.
MACHINE_BUDGET_SECONDS = 180

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


async def _still_running(uow_factory: UnitOfWorkFactory, machine_id: UUID) -> bool:
    """Read again, now: the metering pass saw it resumed, but the release of
    idle machines runs between the two, and restarting a service on a machine
    released this tick would wake it straight back up."""
    from app.modules.workspace.domain.sandbox import (
        SandboxDesiredState,
        SandboxInstanceState,
    )
    from app.modules.workspace.infrastructure.sandbox_repository import (
        SandboxRepository,
    )

    async with uow_factory() as uow:
        repository = SandboxRepository(uow)
        sandbox = await repository.get(machine_id)
        instance = await repository.current_instance(machine_id)
    return (
        sandbox is not None
        and sandbox.desired_state is SandboxDesiredState.PRESENT
        and instance is not None
        and instance.state is SandboxInstanceState.READY
    )


async def _resume_one(uow_factory: UnitOfWorkFactory, machine: Machine) -> int:
    from app.modules.workspace.services.machine_service_starter import (
        starter_may_restart,
    )

    if machine.template is MachineTemplate.WORKSPACE_DOCKER:
        await run_on_machine(
            machine.id,
            acting_user=_acting_user(machine),
            command=DOCKER_AFTER_RESUME,
            timeout_seconds=120,
        )
    restarted = await MachineProcesses(
        services=MachineServices(uow_factory)
    ).restart_services(
        machine.id,
        may_restart=lambda definition: starter_may_restart(uow_factory, definition),
    )
    if await LoginBackups(uow_factory).restore_if_lost(
        machine.id, acting_user=_acting_user(machine)
    ):
        logger.info(
            "workspace.machine_housekeeping.logins_restored.observed",
            sandbox_id=str(machine.id),
        )
    return len(restarted)


async def after_resume(uow_factory: UnitOfWorkFactory, machine_ids: list[UUID]) -> None:
    """Services back up, Docker answering, and the logins back if the disk
    came up without them, on machines that just started or resumed."""
    for machine_id in machine_ids:
        try:
            machine = await _machine(uow_factory, machine_id)
            if machine is None or not await _still_running(uow_factory, machine_id):
                continue
            async with asyncio.timeout(MACHINE_BUDGET_SECONDS):
                restarted = await _resume_one(uow_factory, machine)
        except PER_MACHINE_FAILURES as exc:
            logger.warning(
                "workspace.machine_housekeeping.resume_failed.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            continue
        if restarted:
            logger.info(
                "workspace.machine_housekeeping.services_restarted.observed",
                sandbox_id=str(machine_id),
                restarted_count=restarted,
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
        if await _hand_to_size_move(uow_factory, machine):
            return
        await MachineLifecycle(uow_factory).release(
            machine, acting_user=_acting_user(machine)
        )

    return release


async def _hand_to_size_move(uow_factory: UnitOfWorkFactory, machine: Machine) -> bool:
    """Whether this idle machine is the size-move job's now: one already in
    flight, or a default machine at the legacy size, marked here. The job
    does the work, from the state on the row -- never this sweep.

    Marked only when no other move is waiting: a marked machine is refused
    to its owner until its move ends. Not marked again once abandoned for
    the same size, so it is released like any other machine."""
    if not workspace_settings.machine_size_migration_enabled:
        return False
    from app.modules.workspace.services.size_migration import (
        MARKED_AT_ONCE,
        SizeMigration,
    )

    migration = SizeMigration(uow_factory)
    if await migration.in_flight(machine.id):
        return True
    if machine.kind is not MachineKind.DEFAULT or machine.size != LEGACY_DEFAULT_SIZE:
        return False
    if await migration.waiting() >= MARKED_AT_ONCE:
        return False
    from app.modules.workspace.services.machine_policy import policy_for_user

    async with uow_factory() as uow:
        target = (await policy_for_user(uow, machine.owner_id)).default_size
    if target == machine.size:
        return False
    return await migration.begin(machine, to=target)


async def slot_held(machine_id: UUID) -> bool:
    from app.modules.workspace.services.run_slots import RunSlots

    return bool(await RunSlots().holders(machine_id))


def service_idle(uow_factory: UnitOfWorkFactory):
    """For the idle sweep: whether a running process is a service that has
    written nothing for ``MACHINE_SERVICE_IDLE_SECONDS``. Anything that cannot
    be read counts as in use -- an unreadable process is not an idle one."""
    idle_after = timedelta(seconds=workspace_settings.machine_service_idle_seconds)

    async def idle(machine_id: UUID, process_id: str) -> bool:
        services = MachineServices(uow_factory)
        try:
            cursor = await services.output_cursor(machine_id, process_id)
            if cursor is None:
                return False
            snapshot = await MachineProcesses(services=services).output(
                machine_id, process_id, after_sequence=cursor, wait_seconds=0
            )
            return not await services.active(
                machine_id,
                process_id,
                next_sequence=snapshot.next_sequence,
                idle_after=idle_after,
            )
        except PER_MACHINE_FAILURES:
            logger.warning(
                "workspace.machine_housekeeping.service_idle_unknown.degraded",
                sandbox_id=str(machine_id),
                exc_info=True,
            )
            return False

    return idle


async def daily_login_copies(
    uow_factory: UnitOfWorkFactory, machine_ids: list[UUID]
) -> int:
    """The once-a-day copy, for machines that are running anyway. Never wakes one."""
    backups = LoginBackups(uow_factory)
    taken = 0
    for machine_id in machine_ids:
        try:
            machine = await _machine(uow_factory, machine_id)
            if machine is None or not await backups.due(machine_id, reason="daily"):
                continue
            async with asyncio.timeout(MACHINE_BUDGET_SECONDS):
                result = await backups.back_up(
                    machine_id, acting_user=_acting_user(machine), reason="daily"
                )
            if result.stored:
                taken += 1
        except PER_MACHINE_FAILURES as exc:
            logger.warning(
                "workspace.machine_housekeeping.daily_copy_failed.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
    return taken

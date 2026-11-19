"""Moving a default machine to a new size without losing its home or its logins.

Where the disk outlives the compute (Docker, the desktop guest) a size is a
property of the next container: the machine is rebuilt on the same volume and
nothing has to move. Where the sandbox *is* the disk (E2B) the home has to be
carried: packed into a tarball on the old machine, streamed to object storage,
unpacked onto the new one -- package caches left behind, since they are most
of the bytes and rebuilt by the next install. The logins travel the way they
travel everywhere: a fresh encrypted copy before, restored after.

A move is a sequence of steps that can each take an hour, and the one in the
middle destroys a disk. So it is a state on the machine row, written before
each step's effects can be lost, and a job of its own resumes it from there
(``workspace.machine_size_migrations``):

1. ``started`` -- marked by the sweep on an idle default machine. The logins
   are copied off (a failed copy stops here, disk untouched) and the home is
   packed and uploaded; the object key is written to the row with ``packed``.
2. ``packed`` -- the old machine is destroyed and the row takes the new size
   (and a fresh storage generation when the home was carried) as ``resized``,
   in one write. A crash between the destroy and the write repeats the
   destroy, which is harmless; the carried home is still in object storage.
3. ``resized`` -- the new machine is started, the home unpacked and the
   logins restored, the machine released, and the state cleared -- and only
   then is the carried copy deleted.

A step that fails is retried on the job's next run, up to ``MAX_ATTEMPTS``;
a move that never got past ``started`` is then abandoned (nothing was lost),
and one past it is left for a person to look at, with its home still in
object storage and the row naming it. Behind ``MACHINE_SIZE_MIGRATION_ENABLED``.
"""

from __future__ import annotations

import asyncio
import shlex
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select, update

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.workspace.domain.machine import Machine, MachineSize
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.infrastructure.models import SandboxModel
from app.modules.workspace.providers.base import ProviderStorageKind, storage_kind_for
from app.modules.workspace.services.login_backup import LoginBackups
from app.modules.workspace.services.machine_shell import run_on_machine
from sandbox_runtime.paths import CACHE_PATHS, HOME_ROOT, PROTECTED_PATHS

logger = get_logger(__name__)

_PACKED = "/tmp/lemma-home-carry.tgz"
_PREFIX = "machine-home-carry"
#: The largest home carried. Past it the machine keeps its size, and says so
#: in the log, rather than spending an hour streaming a disk.
MAX_CARRY_BYTES = 8 * 1024 * 1024 * 1024
#: Tries per move before it is abandoned (``started``) or left for a person.
MAX_ATTEMPTS = 5
#: The longest one machine's move may run in one job: the step timeouts below
#: add up to three hours at most.
MACHINE_BUDGET = timedelta(hours=3, minutes=30)
_LOCK = "machine:size-move:v1:{machine_id}"
#: Moves looked at per job; the job starts new ones only in its first half
#: hour, so more than this could never begin in one run anyway.
_PENDING_PAGE = 50


class MoveState(StrEnum):
    STARTED = "started"
    PACKED = "packed"
    RESIZED = "resized"


def _store():
    return build_object_store(
        local_prefix=local_object_storage_path(_PREFIX), remote_prefix=_PREFIX
    )


def _pack_command() -> str:
    """Everything under the home but caches and the browser's profile, which
    travels encrypted on its own."""
    excluded = [*CACHE_PATHS, *PROTECTED_PATHS]
    excludes = " ".join(
        f"--exclude={shlex.quote('./' + path.removeprefix(HOME_ROOT + '/'))}"
        for path in excluded
    )
    return (
        f"tar czf {_PACKED} {excludes} -C {shlex.quote(HOME_ROOT)} . "
        f"&& stat -c %s {_PACKED}"
    )


def _deadline(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class SizeMigration:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def _service(self):
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        return get_sandbox_service()

    # --- the state on the row -------------------------------------------------

    async def begin(self, machine: Machine, *, to: MachineSize) -> bool:
        """Mark a move. False when one is already marked."""
        async with self._uow_factory() as uow:
            marked = await uow.session.execute(
                update(SandboxModel)
                .where(
                    SandboxModel.id == machine.id,
                    SandboxModel.size_migration_state.is_(None),
                )
                .values(
                    size_migration_state=MoveState.STARTED.value,
                    size_migration_target=to.label,
                    size_migration_carry_key=None,
                    size_migration_attempts=0,
                    size_migration_started_at=datetime.now(timezone.utc),
                )
            )
            await uow.commit()
        return bool(getattr(marked, "rowcount", 0))

    async def in_flight(self, machine_id: UUID) -> bool:
        """A move this job still owns: marked, and not given up on."""
        async with self._uow_factory() as uow:
            row = await uow.session.execute(
                select(
                    SandboxModel.size_migration_state,
                    SandboxModel.size_migration_attempts,
                ).where(SandboxModel.id == machine_id)
            )
            found = row.first()
        return found is not None and found[0] is not None and found[1] < MAX_ATTEMPTS

    async def pending(self) -> list[UUID]:
        async with self._uow_factory() as uow:
            rows = await uow.session.scalars(
                select(SandboxModel.id)
                .where(
                    SandboxModel.size_migration_state.is_not(None),
                    SandboxModel.size_migration_attempts < MAX_ATTEMPTS,
                )
                .order_by(SandboxModel.size_migration_started_at)
                .limit(_PENDING_PAGE)
            )
            return list(rows.all())

    async def _row(self, machine_id: UUID) -> SandboxModel | None:
        async with self._uow_factory() as uow:
            return await uow.session.get(SandboxModel, machine_id)

    async def _set(self, machine_id: UUID, **values: object) -> None:
        async with self._uow_factory() as uow:
            await uow.session.execute(
                update(SandboxModel)
                .where(SandboxModel.id == machine_id)
                .values(**values, updated_at=datetime.now(timezone.utc))
            )
            await uow.commit()

    # --- running it -------------------------------------------------------------

    async def migrate(
        self, machine: Machine, *, to: MachineSize, acting_user: UUID
    ) -> bool:
        """Mark and run a whole move now. True when the machine has the new
        size. Leaves it released."""
        await self.begin(machine, to=to)
        return await self.advance(machine.id, acting_user=acting_user)

    async def advance(self, machine_id: UUID, *, acting_user: UUID) -> bool:
        """Carry a marked move as far as it goes, from the state on the row.
        True when it finished; raises what stopped it, with the state kept."""
        row = await self._row(machine_id)
        if row is None or row.size_migration_state is None:
            return False
        await self._set(
            machine_id, size_migration_attempts=row.size_migration_attempts + 1
        )
        state = MoveState(row.size_migration_state)
        target = MachineSize.parse(row.size_migration_target or "")
        carry_key = row.size_migration_carry_key
        if state is MoveState.STARTED:
            proceed, carry_key = await self._pack_step(machine_id, acting_user)
            if not proceed:
                return False
            state = MoveState.PACKED
        if state is MoveState.PACKED:
            await self._destroy_step(machine_id, target, carried=carry_key is not None)
            state = MoveState.RESIZED
        await self._finish_step(machine_id, acting_user, carry_key)
        logger.info(
            "workspace.size_migration.moved.observed",
            sandbox_id=str(machine_id),
            size=target.label,
            carried=carry_key is not None,
        )
        return True

    async def _pack_step(
        self, machine_id: UUID, acting_user: UUID
    ) -> tuple[bool, str | None]:
        """Logins off, home packed and uploaded, key on the row: whether to go
        on, and the carry key. Not on -- the move abandoned -- when the home
        is too big or will not pack."""
        from app.modules.workspace.services.run_slots import RunSlots

        if await RunSlots().holders(machine_id):
            raise MachineConflict(
                "A run is using this machine; the move waits for it.",
                code="MACHINE_BUSY",
            )
        await LoginBackups(self._uow_factory).require_back_up(
            machine_id, acting_user=acting_user, reason="before_resize"
        )
        carries_disk = (
            storage_kind_for(self._service()._provider, machine_id)
            is ProviderStorageKind.SANDBOX_NATIVE
        )
        carry_key = None
        if carries_disk:
            carry_key = await self._pack(machine_id, acting_user)
            if carry_key is None:
                await self._abandon(machine_id)
                return False, None
        await self._set(
            machine_id,
            size_migration_state=MoveState.PACKED.value,
            size_migration_carry_key=carry_key,
        )
        return True, carry_key

    async def _destroy_step(
        self, machine_id: UUID, target: MachineSize, *, carried: bool
    ) -> None:
        from app.modules.workspace.services.compute_meter import close_sessions

        await self._service().destroy(machine_id, delete_storage=carried)
        await close_sessions(self._uow_factory, machine_id)
        values: dict[str, object] = {
            "size": target.label,
            "desired_state": "present",
            "size_migration_state": MoveState.RESIZED.value,
        }
        if carried:
            values["storage_generation"] = SandboxModel.storage_generation + 1
            values["provider_volume_id"] = None
        await self._set(machine_id, **values)

    async def _finish_step(
        self, machine_id: UUID, acting_user: UUID, carry_key: str | None
    ) -> None:
        from app.modules.workspace.infrastructure.machine_repository import (
            MachineRepository,
        )
        from app.modules.workspace.services.machine_lifecycle import MachineLifecycle

        await self._service().ensure(machine_id)
        if carry_key is not None:
            await self._unpack(machine_id, acting_user, carry_key)
            backups = LoginBackups(self._uow_factory)
            if await backups.latest(machine_id) is not None and not (
                await backups.restore(machine_id, acting_user=acting_user)
            ):
                raise MachineConflict(
                    "The logins could not be restored onto the new disk; the "
                    "move will be tried again.",
                    code="MACHINE_RESIZE_RESTORE_FAILED",
                )
        async with self._uow_factory() as uow:
            machine = await MachineRepository(uow).get(machine_id)
        if machine is not None:
            await MachineLifecycle(self._uow_factory).release(
                machine, acting_user=acting_user
            )
        # The row first: a key cleared with its object still stored is a few
        # orphaned bytes; an object deleted under a key still named is a move
        # that can never finish.
        await self._set(
            machine_id,
            size_migration_state=None,
            size_migration_target=None,
            size_migration_carry_key=None,
            size_migration_attempts=0,
            size_migration_started_at=None,
        )
        if carry_key is not None:
            await _forget_carry(carry_key)

    async def _abandon(self, machine_id: UUID) -> None:
        await self._set(
            machine_id,
            size_migration_state=None,
            size_migration_target=None,
            size_migration_carry_key=None,
            size_migration_started_at=None,
        )

    async def _pack(self, machine_id: UUID, acting_user: UUID) -> str | None:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        packed = await run_on_machine(
            machine_id,
            acting_user=acting_user,
            command=_pack_command(),
            timeout_seconds=1800,
        )
        try:
            size = int(packed.stdout.strip().splitlines()[-1])
        except IndexError, ValueError:
            size = -1
        if not packed.ok or size < 0 or size > MAX_CARRY_BYTES:
            logger.warning(
                "workspace.size_migration.home_not_carried.degraded",
                sandbox_id=str(machine_id),
                exit_code=packed.exit_code,
                size_bytes=size,
            )
            await run_on_machine(
                machine_id,
                acting_user=acting_user,
                command=f"rm -f {_PACKED}",
                timeout_seconds=30,
            )
            return None
        object_key = f"{machine_id}/{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.tgz"
        client = build_local_client()
        async with client.stream_file(
            machine_id, _PACKED, deadline_at=_deadline(3600)
        ) as stream:
            await _store().put_async(object_key, stream)
        return object_key

    async def _unpack(
        self, machine_id: UUID, acting_user: UUID, object_key: str
    ) -> None:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        result = await _store().get_async(object_key)
        await build_local_client().write_file_stream(
            machine_id, _PACKED, _chunks(result), deadline_at=_deadline(3600)
        )
        unpacked = await run_on_machine(
            machine_id,
            acting_user=acting_user,
            command=f"tar xzf {_PACKED} -C {shlex.quote(HOME_ROOT)} && rm -f {_PACKED}",
            timeout_seconds=1800,
        )
        if not unpacked.ok:
            raise MachineConflict(
                "The machine's files could not be unpacked onto its new disk; "
                "they are kept and the move will be tried again.",
                code="MACHINE_RESIZE_UNPACK_FAILED",
            )


async def _forget_carry(object_key: str) -> None:
    try:
        await _store().delete_async(object_key)
    except FileNotFoundError, OSError:
        logger.warning("workspace.size_migration.carry_delete.degraded", exc_info=True)


async def _chunks(result):
    async for chunk in result:
        yield bytes(chunk)


async def _claim(machine_id: UUID) -> bool:
    """One job per machine at a time: a periodic run that outlives its interval
    must not start the same move twice."""
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis

    return bool(
        await get_redis(url=settings.redis_url).set(
            _LOCK.format(machine_id=machine_id),
            "1",
            nx=True,
            ex=int(MACHINE_BUDGET.total_seconds()) + 600,
        )
    )


async def _unclaim(machine_id: UUID) -> None:
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis

    await get_redis(url=settings.redis_url).delete(_LOCK.format(machine_id=machine_id))


def _move_failures() -> tuple[type[BaseException], ...]:
    from redis.exceptions import RedisError
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.workspace.contracts.runtime_errors import machine_runtime_errors
    from app.modules.workspace.services.login_backup import LoginBackupFailed

    return (
        MachineConflict,
        LoginBackupFailed,
        OSError,
        ValueError,
        RedisError,
        SQLAlchemyError,
        TimeoutError,
        *machine_runtime_errors(),
    )


async def run_pending_moves(
    uow_factory: UnitOfWorkFactory, *, start_new_for: timedelta = timedelta(minutes=30)
) -> int:
    """The job: every marked move, one machine at a time, each within its own
    budget. A new one is started only in the job's first ``start_new_for``, so
    the job ends inside its own time limit. Returns how many finished."""
    migration = SizeMigration(uow_factory)
    began = datetime.now(timezone.utc)
    finished = 0
    for machine_id in await migration.pending():
        if datetime.now(timezone.utc) - began > start_new_for:
            break
        if not await _claim(machine_id):
            continue
        try:
            row = await migration._row(machine_id)
            acting_user = (row.created_by or row.owner_id) if row else None
            if acting_user is None:
                continue
            async with asyncio.timeout(MACHINE_BUDGET.total_seconds()):
                finished += int(
                    await migration.advance(machine_id, acting_user=acting_user)
                )
        except _move_failures() as exc:
            await _note_failure(migration, machine_id, exc)
        finally:
            await _unclaim(machine_id)
    return finished


async def _note_failure(
    migration: SizeMigration, machine_id: UUID, exc: BaseException
) -> None:
    row = await migration._row(machine_id)
    attempts = row.size_migration_attempts if row else 0
    state = row.size_migration_state if row else None
    if attempts >= MAX_ATTEMPTS and state == MoveState.STARTED.value:
        # Nothing destroyed yet: give the move up and keep the machine as it is.
        await migration._abandon(machine_id)
    elif attempts >= MAX_ATTEMPTS:
        # Past the destroy: the home is in object storage under the key on the
        # row, and a person has to finish this.
        logger.error(
            "workspace.size_migration.stuck",
            sandbox_id=str(machine_id),
            state=state,
            attempts=attempts,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return
    logger.warning(
        "workspace.size_migration.step_failed.degraded",
        sandbox_id=str(machine_id),
        state=state,
        attempts=attempts,
        error_type=type(exc).__name__,
        exc_info=exc,
    )

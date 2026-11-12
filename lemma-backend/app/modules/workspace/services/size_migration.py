"""Moving a default machine to a new size without losing its home or its logins.

Where the disk outlives the compute (Docker, the desktop guest) a size is a
property of the next container: the machine is rebuilt on the same volume and
nothing has to move. Where the sandbox *is* the disk (E2B) the home has to be
carried: packed into a tarball on the old machine, streamed to object storage,
unpacked onto the new one -- package caches left behind, since they are most
of the bytes and rebuilt by the next install. The logins travel the way they
travel everywhere: a fresh encrypted copy before, restored after.

Run by the sweeper on a default machine's next idle release, never under
someone's work, and only while ``MACHINE_SIZE_MIGRATION_ENABLED`` says the
login backup it depends on is live.
"""

from __future__ import annotations

import shlex
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.object_storage import build_object_store, local_object_storage_path
from app.modules.workspace.domain.machine import Machine, MachineSize
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.providers.base import ProviderStorageKind, storage_kind_for
from app.modules.workspace.services.login_backup import (
    LoginBackups,
    close_browser_command,
)
from app.modules.workspace.services.machine_shell import run_on_machine
from sandbox_runtime.paths import CACHE_PATHS, HOME_ROOT, PROTECTED_PATHS

logger = get_logger(__name__)

_PACKED = "/tmp/lemma-home-carry.tgz"
_PREFIX = "machine-home-carry"
#: The largest home carried. Past it the machine keeps its size, and says so
#: in the log, rather than spending an hour streaming a disk.
MAX_CARRY_BYTES = 8 * 1024 * 1024 * 1024


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

    async def migrate(
        self, machine: Machine, *, to: MachineSize, acting_user: UUID
    ) -> bool:
        """True when the machine now has the new size. Leaves it released."""
        service = self._service()
        backups = LoginBackups(self._uow_factory)
        await run_on_machine(
            machine.id,
            acting_user=acting_user,
            command=close_browser_command(),
            timeout_seconds=30,
        )
        await backups.back_up(
            machine.id, acting_user=acting_user, reason="before_resize", force=True
        )
        carries_disk = (
            storage_kind_for(service._provider, machine.id)
            is ProviderStorageKind.SANDBOX_NATIVE
        )
        object_key = None
        if carries_disk:
            object_key = await self._pack(machine.id, acting_user)
            if object_key is None:
                return False
        await service.destroy(machine.id, delete_storage=carries_disk)
        await self._resize_row(machine.id, to, fresh_disk=carries_disk)
        await service.ensure(machine.id)
        if object_key is not None:
            await self._unpack(machine.id, acting_user, object_key)
            await backups.restore(machine.id, acting_user=acting_user)
        from app.modules.workspace.services.machine_lifecycle import MachineLifecycle

        await MachineLifecycle(self._uow_factory).release(
            await self._fresh(machine.id), acting_user=acting_user
        )
        logger.info(
            "workspace.size_migration.moved.observed",
            sandbox_id=str(machine.id),
            size=to.label,
            carried=carries_disk,
        )
        return True

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
        await _store().delete_async(object_key)

    async def _resize_row(
        self, machine_id: UUID, size: MachineSize, *, fresh_disk: bool
    ) -> None:
        from app.modules.workspace.infrastructure.machine_repository import (
            MachineRepository,
        )
        from app.modules.workspace.infrastructure.models import SandboxModel

        values: dict[str, object] = {"size": size.label, "desired_state": "present"}
        if fresh_disk:
            values["storage_generation"] = SandboxModel.storage_generation + 1
            values["provider_volume_id"] = None
        async with self._uow_factory() as uow:
            await MachineRepository(uow).update_machine(machine_id, **values)
            await uow.commit()

    async def _fresh(self, machine_id: UUID) -> Machine:
        from app.modules.workspace.infrastructure.machine_repository import (
            MachineRepository,
        )

        async with self._uow_factory() as uow:
            machine = await MachineRepository(uow).get(machine_id)
        assert machine is not None
        return machine


async def _chunks(result):
    async for chunk in result:
        yield bytes(chunk)

"""Stopping, restarting and cleaning a machine without losing a login.

Every path that stops a machine or replaces its disk comes through here, and
does it in the same order: close the browser cleanly (agent-browser writes the
profile back only on a clean close), copy the logins off the machine, and only
then stop it. A machine is never paused with a browser open -- that is how a
login was lost before: the profile sat in the browser's scratch directory and
the pause ended it there.

Reset scopes, and what each touches:

- ``processes``: every process but services; for an agent, only the
  processes its own run started;
- ``runs``: finished runs' working directories under ``~/lemma/runs`` --
  never one a live run holds a slot for, and never one with a file written
  in the last hour;
- ``caches``: package-manager and tool caches (`CACHE_PATHS`);
- ``docker``: stopped containers, unused images and the build cache --
  everyone's, which is why only a machine's managers may run it;
- ``browser-session``: the browser's open tabs, closed cleanly -- the logins
  stay;
- ``home``: a fresh disk. Needs ``confirm``; the logins are copied off first
  and restored onto the new disk unless ``forget_logins``.

None but ``home`` goes near a protected path, and none of the commands below
names one.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.domain.machine import Machine
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services.login_backup import (
    LoginBackupFailed,
    LoginBackups,
    close_browser_command,
)
from app.modules.workspace.services.machine_processes import MachineProcesses
from app.modules.workspace.services.machine_shell import (
    DISK_USED,
    run_on_machine,
)
from app.modules.workspace.providers.runtime_client import WorkspaceRuntimeError
from sandbox_runtime.errors import SandboxError
from sandbox_runtime.paths import CACHE_PATHS, RUNS_ROOT

logger = get_logger(__name__)

#: What a clean close can fail with and still let the stop go on: the machine
#: not answering, its runtime refusing, the object store or the cipher.
_CLEAN_CLOSE_FAILURES: tuple[type[BaseException], ...] = (
    SandboxError,
    WorkspaceRuntimeError,
    OSError,
    ValueError,
)

#: What an agent may clean on a machine it uses. Each still costs somebody
#: something -- a cache rebuilt on the next install, a finished run's files --
#: but nothing another run is using: ``processes`` is limited to the agent's
#: own run, ``runs`` skips live runs. ``docker`` prunes every run's images
#: and build cache, so it is a manager's, not here.
AGENT_SCOPES = frozenset({"processes", "runs", "caches"})

#: A run directory is finished when no file under it has been written for an
#: hour and no live run holds a slot for it. Neither alone is enough: a run
#: compiling for an hour writes nothing to its own directory's mtime, and a
#: slot lease lapses under a run that is only waiting.
_FINISHED_RUN_MINUTES = 60


def _runs_prune(keep: frozenset[str]) -> str:
    root = shlex.quote(RUNS_ROOT)
    kept = shlex.quote(" " + " ".join(sorted(keep)) + " ")
    return (
        f"[ -d {root} ] || exit 0; "
        f'for d in {root}/*/*/; do d=${{d%/}}; [ -d "$d" ] || continue; '
        f'case {kept} in *" ${{d##*/}} "*) continue;; esac; '
        f'[ -n "$(find "$d" -mmin -{_FINISHED_RUN_MINUTES} -print -quit 2>/dev/null)" ] '
        "&& continue; "
        'rm -rf "$d"; done; true'
    )


def scope_command(scope: str, *, keep: frozenset[str] = frozenset()) -> str | None:
    """The shell for one scope. ``keep`` names run directories (by run id)
    that ``runs`` must leave: the machine's current slot holders."""
    if scope == "runs":
        return _runs_prune(keep)
    if scope == "caches":
        return (
            "rm -rf " + " ".join(shlex.quote(path) for path in CACHE_PATHS) + "; true"
        )
    if scope == "docker":
        return (
            "command -v docker >/dev/null || exit 0; "
            "docker container prune -f >/dev/null; docker image prune -af >/dev/null; "
            "docker builder prune -af >/dev/null; true"
        )
    if scope == "browser-session":
        return close_browser_command()
    return None


@dataclass(frozen=True, slots=True)
class ScopeOutcome:
    scope: str
    freed_bytes: int | None = None
    detail: str | None = None


class MachineLifecycle:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        processes: MachineProcesses | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._backups = LoginBackups(uow_factory)
        self._processes = processes or MachineProcesses()

    def _service(self):
        from app.modules.workspace.services.sandbox_composition import (
            get_sandbox_service,
        )

        return get_sandbox_service()

    async def close_browser_and_back_up(
        self, machine_id: UUID, *, acting_user: UUID, reason: str
    ) -> None:
        """The clean close every stop goes through. Best effort, never blocking
        the stop: a stop keeps the disk, and a machine that cannot be reached
        cannot lose anything more by being stopped than it already has. A
        browser that would not let go of its profile is not copied -- the
        copies already kept are better than one taken under a live Chrome."""
        try:
            closed = await run_on_machine(
                machine_id,
                acting_user=acting_user,
                command=close_browser_command(),
                timeout_seconds=40,
            )
            if not closed.ok:
                logger.warning(
                    "workspace.machine_lifecycle.browser_close_refused.degraded",
                    sandbox_id=str(machine_id),
                    exit_code=closed.exit_code,
                )
                return
            await self._backups.back_up(
                machine_id, acting_user=acting_user, reason=reason
            )
        except _CLEAN_CLOSE_FAILURES as exc:
            logger.warning(
                "workspace.machine_lifecycle.clean_close_failed.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    async def release(self, machine: Machine, *, acting_user: UUID) -> None:
        await self.close_browser_and_back_up(
            machine.id, acting_user=acting_user, reason="clean_close"
        )
        await self._service().release(machine.id)
        from app.modules.workspace.services.compute_meter import close_sessions

        await close_sessions(self._uow_factory, machine.id)

    async def restart(self, machine: Machine, *, acting_user: UUID) -> None:
        await self.release(machine, acting_user=acting_user)
        await self._service().ensure(machine.id)

    async def reset(
        self,
        machine: Machine,
        scopes: list[str],
        *,
        acting_user: UUID,
        confirm: bool = False,
        forget_logins: bool = False,
        run_id: str | None = None,
    ) -> list[ScopeOutcome]:
        """``run_id`` limits ``processes`` to one run's: an agent's reset
        never stops another run's build."""
        if "home" in scopes:
            if not confirm:
                raise MachineConflict(
                    "Resetting the home replaces the machine's disk. "
                    "Send confirm=true to do it.",
                    code="MACHINE_RESET_NEEDS_CONFIRM",
                )
            return [await self._reset_home(machine, acting_user, forget_logins)]
        outcomes: list[ScopeOutcome] = []
        for scope in scopes:
            outcomes.append(
                await self._reset_scope(machine, scope, acting_user, run_id=run_id)
            )
        return outcomes

    async def _reset_scope(
        self, machine: Machine, scope: str, acting_user: UUID, *, run_id: str | None
    ) -> ScopeOutcome:
        if scope == "processes":
            killed = (
                await self._processes.end_run(machine.id, run_id)
                if run_id is not None
                else await self._processes.kill_all(machine.id, keep_services=True)
            )
            count = len(killed)
            return ScopeOutcome(
                scope,
                detail=f"stopped {count} process{'' if count == 1 else 'es'}",
            )
        if scope == "docker" and not machine.capabilities.docker:
            # Said, rather than run: the prune finds no daemon, frees nothing,
            # and reported that as a clean success on a machine with no Docker.
            return ScopeOutcome(scope, detail="this machine has no Docker")
        before = await self._disk_used(machine.id, acting_user)
        command = scope_command(scope, keep=await slot_holder_runs(machine.id))
        if command is None:
            return ScopeOutcome(scope, detail="nothing to do")
        outcome = await run_on_machine(
            machine.id, acting_user=acting_user, command=command, timeout_seconds=600
        )
        after = await self._disk_used(machine.id, acting_user)
        freed = before - after if before is not None and after is not None else None
        return ScopeOutcome(
            scope,
            freed_bytes=max(0, freed) if freed is not None else None,
            detail=None if outcome.ok else (outcome.stderr.strip()[:300] or None),
        )

    async def _reset_home(
        self, machine: Machine, acting_user: UUID, forget_logins: bool
    ) -> ScopeOutcome:
        if forget_logins:
            await self._backups.forget(machine.id)
        else:
            await self._require_login_copy(machine.id, acting_user)
        service = self._service()
        await service.destroy(machine.id, delete_storage=True)
        from app.modules.workspace.services.compute_meter import close_sessions

        await close_sessions(self._uow_factory, machine.id)
        # What ran on the old disk is not started on the new one: its files
        # are gone, and so is whatever made it worth bringing back.
        await self._forget_services(machine.id)
        await _bump_storage(self._uow_factory, machine.id)
        await service.ensure(machine.id)
        restored = (
            False
            if forget_logins
            else await self._backups.restore(machine.id, acting_user=acting_user)
        )
        return ScopeOutcome(
            "home",
            detail="logins restored" if restored else "fresh disk, no logins",
        )

    async def _require_login_copy(self, machine_id: UUID, acting_user: UUID) -> None:
        """A disk is replaced only with its logins copied off, or with none to
        copy. Anything else stops the reset with the disk as it was."""
        try:
            await self._backups.require_back_up(
                machine_id, acting_user=acting_user, reason="before_reset"
            )
        except (LoginBackupFailed, *_CLEAN_CLOSE_FAILURES) as exc:
            logger.warning(
                "workspace.machine_lifecycle.reset_kept_disk.degraded",
                sandbox_id=str(machine_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise MachineConflict(
                str(exc)
                if isinstance(exc, LoginBackupFailed)
                else "The machine did not answer, so its browser's logins could "
                "not be copied off. The disk was left as it was; try again.",
                code="MACHINE_LOGIN_BACKUP_FAILED",
            ) from exc

    async def _disk_used(self, machine_id: UUID, acting_user: UUID) -> int | None:
        outcome = await run_on_machine(
            machine_id, acting_user=acting_user, command=DISK_USED, timeout_seconds=30
        )
        try:
            return int(outcome.stdout.strip().split()[-1])
        except IndexError, ValueError:
            return None

    async def delete(self, machine: Machine) -> None:
        """A pod machine, gone: compute and disk. Its row stays DELETED so the
        sweep can still recognise anything of it left at the provider."""
        from app.modules.workspace.services.machine_previews import MachinePreviews

        # First, so its preview URLs stop answering before the machine goes.
        await MachinePreviews(self._uow_factory).revoke_all(machine.id)
        await self._service().destroy(machine.id, delete_storage=True)
        from app.modules.workspace.services.compute_meter import close_sessions

        await close_sessions(self._uow_factory, machine.id)
        await self._backups.forget(machine.id)
        await self._forget_services(machine.id)

    async def _forget_services(self, machine_id: UUID) -> None:
        """Its services end with the machine's life (``forget_services``)."""
        from app.modules.workspace.services.machine_services import MachineServices

        await MachineProcesses(
            self._processes.registry, services=MachineServices(self._uow_factory)
        ).forget_services(machine_id)


async def slot_holder_runs(machine_id: UUID) -> frozenset[str]:
    """Runs holding a slot on this machine now: their directories are live."""
    from app.modules.workspace.services.run_slots import RunSlots

    return frozenset(await RunSlots().holders(machine_id))


async def _bump_storage(uow_factory: UnitOfWorkFactory, machine_id: UUID) -> None:
    """A destroyed disk is a new storage generation, and the next provision
    must not adopt a volume by the old name."""
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )
    from app.modules.workspace.infrastructure.models import SandboxModel

    async with uow_factory() as uow:
        await MachineRepository(uow).update_machine(
            machine_id,
            storage_generation=SandboxModel.storage_generation + 1,
            provider_volume_id=None,
            desired_state="present",
        )
        await uow.commit()

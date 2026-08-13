"""Reclaiming compute nobody is using, and compute nobody owns.

Two sweeps, deliberately separate because they answer different questions.

*Idle release* asks "is anyone using this?" and stops sandboxes that have gone
quiet. The disk is kept, so the answer being wrong costs a cold start, not
data.

*Orphan reclamation* asks "does anything still own this?" and destroys provider
objects with no live row behind them. This one is about money: a container the
control plane has forgotten runs, and bills, forever.

Together these replace a reconciler, an inventory sweeper, and a maintenance
worker. They can be this small because deterministic naming means the container
name states which sandbox and which epoch it belongs to, so deciding whether an
object is owned is a lookup rather than a repair.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.log.log import get_logger
from app.modules.workspace.domain.sandbox import SandboxDesiredState
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderInstance,
    ProviderNotReady,
    ProviderRejected,
)
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)


class SandboxSweeper:
    def __init__(self, *, service, uow_factory) -> None:
        self._service = service
        self._uow_factory = uow_factory

    @property
    def _provider(self):
        return self._service._provider

    async def release_idle(self, *, idle_after_seconds: int, limit: int = 50) -> int:
        """Stop sandboxes nobody has touched recently. Keeps every disk."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=idle_after_seconds)
        async with self._uow_factory() as uow:
            stale = await SandboxRepository(uow).list_idle(
                idle_before=cutoff, limit=limit
            )

        released = 0
        for sandbox in stale:
            try:
                if await self._is_busy(sandbox):
                    continue
                await self._service.release(sandbox.id)
            except Exception as exc:
                # One unreachable sandbox must not stop the others being
                # reclaimed; the next sweep will try it again.
                logger.warning(
                    "workspace.sandbox_sweeper.idle_release_failed",
                    sandbox_id=str(sandbox.id),
                    error_type=type(exc).__name__,
                )
                continue
            released += 1
        return released

    async def _is_busy(self, sandbox) -> bool:
        """Is something still running inside this sandbox?

        Idle is measured from the last time a caller asked for the sandbox, not
        from the last thing it did -- so a single long tool call or function
        invocation looks idle the whole time it runs. Without this check the
        sweep would stop compute underneath live work.
        """

        handle = await self._service.describe(sandbox.id)
        if handle is None:
            return False
        instance = ProviderInstance(
            provider_id=handle.name, name=handle.name, running=True
        )
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=15)
        try:
            processes = await self._provider.list_processes(
                instance, deadline_at=deadline_at
            )
        except (SandboxError, ProviderFailed, ProviderGone, ProviderNotReady,
                ProviderRejected):
            # An unreachable sandbox is not evidence that it is idle, and
            # releasing on a failed probe is the mistake this guards against.
            return True
        return any(p.exit_code is None for p in processes)

    async def reclaim_orphans(self, *, dry_run: bool = False) -> tuple[str, ...]:
        """Destroy provider objects no live sandbox row accounts for.

        An object is orphaned when it belongs to a sandbox that no longer
        exists or is deleted, or when it is behind the sandbox's current epoch.
        Anything this module cannot identify is left strictly alone -- a sweep
        that guesses would delete somebody else's containers.
        """
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        objects = await self._provider.list_objects(deadline_at=deadline_at)

        # Containers first. A volume in use cannot be deleted, so reclaiming the
        # container that mounts it in the same pass is what makes its disk
        # collectable now rather than one sweep later.
        ordered = sorted(objects, key=lambda item: item.kind == "volume")

        reclaimed: list[str] = []
        for obj in ordered:
            if obj.sandbox_id is None:
                # Not identifiable as ours. Leaving a stray object running is
                # recoverable; deleting a stranger's container is not.
                continue

            async with self._uow_factory() as uow:
                repository = SandboxRepository(uow)
                sandbox = await repository.get(obj.sandbox_id)
                instance = (
                    await repository.current_instance(obj.sandbox_id)
                    if sandbox is not None
                    else None
                )

            reason = self._reclaim_reason(obj, sandbox, instance)
            if reason is None:
                continue

            reclaimed.append(obj.name)
            if dry_run:
                continue
            try:
                if obj.kind == "volume":
                    await self._provider.destroy_volume(
                        obj.name, deadline_at=deadline_at
                    )
                else:
                    await self._provider.destroy(obj.name, deadline_at=deadline_at)
            except Exception as exc:
                # A volume still mounted by a container Docker has not finished
                # removing refuses deletion; the next sweep collects it.
                logger.warning(
                    "workspace.sandbox_sweeper.orphan_destroy_failed",
                    sandbox_id=str(obj.sandbox_id),
                    error_type=type(exc).__name__,
                )
                continue
            logger.info(
                "workspace.sandbox_sweeper.orphan_reclaimed",
                sandbox_id=str(obj.sandbox_id),
                reason=reason,
            )
        return tuple(reclaimed)

    @staticmethod
    def _reclaim_reason(obj, sandbox, instance) -> str | None:
        """Why this object is reclaimable, or None to leave it alone."""
        if sandbox is None:
            return "no sandbox row"
        if sandbox.desired_state is SandboxDesiredState.DELETED:
            return "sandbox deleted"

        if obj.kind == "volume":
            # A volume is the workspace's disk: it deliberately outlives every
            # container that mounts it, so it is NOT superseded by an epoch
            # bump. Only a newer storage generation makes one obsolete, and a
            # volume whose generation cannot be read is never judged by it --
            # unparseable means unknown, not old.
            if (
                obj.storage_generation is not None
                and obj.storage_generation < sandbox.storage_generation
            ):
                return (
                    f"storage generation {obj.storage_generation} is behind "
                    f"{sandbox.storage_generation}"
                )
            return None

        if obj.legacy:
            # A pre-cutover object carries no epoch, so it cannot be judged
            # by one. While both provisioning paths exist it may still be
            # the *only* container serving this sandbox, and destroying it
            # would kill a live workspace. It is only reclaimable once this
            # module has provisioned a replacement.
            if instance is None:
                return None
            return "superseded by the current provisioning path"
        if obj.epoch is not None and obj.epoch < sandbox.epoch:
            return f"epoch {obj.epoch} is behind {sandbox.epoch}"
        return None

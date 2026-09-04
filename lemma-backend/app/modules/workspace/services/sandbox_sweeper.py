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
from app.modules.workspace.domain.sandbox import (
    SandboxDesiredState,
    SandboxInstanceState,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.process_output import TERMINAL_PROCESS_STATES
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderNotReady,
    ProviderRejected,
    ProviderStorageKind,
)
from sandbox_runtime.errors import SandboxError

logger = get_logger(__name__)

# How many unattributed provider ids to name in the aggregated report. Enough to
# identify which environment they belong to, few enough that the line stays
# readable when an account holds hundreds.
_UNATTRIBUTED_SAMPLE = 10

# How far past the idle cutoff a sandbox has to be before an unreachable
# provider stops being treated as "try again next sweep".
#
# "Next sweep will try it again" is the right answer to a blip and the wrong one
# to a provider object that is never coming back: one sandbox failed to release
# every hour for as long as the logs went back, warning identically each time
# and never converging. Past this window the release is recorded locally
# instead. That keeps the disk -- release never deletes storage -- so the cost
# of being wrong is a cold start, and if the compute really is still running,
# the orphan sweep below is what finds a provider object with no live row and
# destroys it.
_UNREACHABLE_GIVE_UP_MULTIPLE = 6


class SandboxSweeper:
    def __init__(self, *, service, uow_factory) -> None:
        self._service = service
        self._uow_factory = uow_factory

    @property
    def _provider(self):
        return self._service._provider

    @property
    def _epoch_is_a_fence(self) -> bool:
        """Whether a behind-the-times epoch means this object is superseded.

        Only where compute and storage are separate objects. `ProviderStorageKind`
        says so itself: on SANDBOX_NATIVE "one object is both … the fence is the
        provider's own id rather than an epoch in a name", and the E2B provider
        repeats it -- "Adoption deliberately ignores the epoch". This sweep did
        not, and the two readings of the same number pointed at opposite
        conclusions about the same live sandbox.

        It resolved the wrong way. The row's epoch advances on every provision,
        including the ones that adopt an existing E2B sandbox, while the epoch
        this compares it against is read from provider metadata that nothing can
        update: the re-stamp is guarded on `set_metadata`, which the E2B SDK does
        not have. So the recorded epoch was frozen at 1 while the row climbed,
        and "epoch 1 is behind 6" became permanently true for every workspace a
        user had kept. The sweep then called destroy on it, every five minutes,
        and on this provider destroying the sandbox destroys the disk.

        It only ever failed to delete them because destroy could not address a
        paused sandbox -- which is a bug that has since been fixed.
        """
        kind = getattr(self._provider, "storage_kind", ProviderStorageKind.VOLUME)
        return kind is not ProviderStorageKind.SANDBOX_NATIVE

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
                # Named, because the count alone is not a diagnosis. When a
                # run died mid-command and the only nearby log line said
                # `released_count=1`, nothing connected the two: establishing
                # whether the sweep had touched *that* sandbox meant querying
                # the database, and by then the row had been re-provisioned.
                logger.info(
                    "workspace.sandbox_sweeper.released_idle_sandbox.observed",
                    sandbox_id=str(sandbox.id),
                    idle_after_seconds=idle_after_seconds,
                )
            except Exception as exc:
                if await self._give_up_on(
                    sandbox, idle_after_seconds=idle_after_seconds, error=exc
                ):
                    released += 1
                    continue
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

    async def _give_up_on(self, sandbox, *, idle_after_seconds: int, error) -> bool:
        """Record the release ourselves when the provider will not answer.

        Only for a sandbox that has been idle far past the cutoff -- see
        `_UNREACHABLE_GIVE_UP_MULTIPLE`. Anything sooner is a blip and is worth
        another sweep.
        """
        last_used_at = getattr(sandbox, "last_used_at", None)
        if last_used_at is None:
            return False
        give_up_after = timedelta(
            seconds=idle_after_seconds * _UNREACHABLE_GIVE_UP_MULTIPLE
        )
        if datetime.now(timezone.utc) - last_used_at < give_up_after:
            return False

        async with self._uow_factory() as uow:
            repository = SandboxRepository(uow)
            instance = await repository.current_instance(sandbox.id)
            if instance is not None:
                await repository.mark_instance_released(instance.id)
            await repository.set_desired_state(sandbox.id, SandboxDesiredState.RELEASED)
            await uow.commit()
        logger.info(
            "workspace.sandbox_sweeper.released_unreachable_sandbox.observed",
            sandbox_id=str(sandbox.id),
            error_type=type(error).__name__,
            idle_after_seconds=idle_after_seconds,
        )
        return True

    async def _is_busy(self, sandbox) -> bool:
        """Is something still running inside this sandbox?

        Idle is measured from the last time a caller asked for the sandbox, not
        from the last thing it did -- so a single long tool call or function
        invocation looks idle the whole time it runs. Without this check the
        sweep would stop compute underneath live work.
        """

        async with self._uow_factory() as uow:
            current = await SandboxRepository(uow).current_instance(sandbox.id)
        if current is None or not current.provider_id:
            return False
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=15)
        try:
            # Ask the provider to resolve it, rather than assembling an
            # instance here. The row records the deterministic container name,
            # which is the provider id on Docker and nothing like it on E2B --
            # there it mints its own (`i8fdef5eyd8zxnysl6bor`) and keys the
            # process index by that. A probe carrying the name read an index
            # that was always empty, so this check answered "idle" for every
            # sandbox it was ever asked about, and the sweep would pause a
            # workspace mid-command. An idle check that returns is not an idle
            # check that happened.
            instance = await self._provider.inspect(
                current.provider_id, deadline_at=deadline_at
            )
            if instance is None:
                # Same policy as the `except` below, which says in as many
                # words that an unreachable sandbox is not evidence that it is
                # idle. This branch said the opposite fifteen lines above it:
                # a transient empty result from the provider's metadata listing
                # read as "not busy" and released a sandbox mid-command.
                return True
            processes = await self._provider.list_processes(
                instance, deadline_at=deadline_at
            )
        except (
            SandboxError,
            ProviderFailed,
            ProviderGone,
            ProviderNotReady,
            ProviderRejected,
        ):
            # An unreachable sandbox is not evidence that it is idle, and
            # releasing on a failed probe is the mistake this guards against.
            return True
        # State, not exit code. A cancelled process on E2B is recorded with
        # `exit_code=None` (`e2b_output.record_cancelled`), so reading busy-ness
        # off the exit code made every process an agent killed pin its sandbox
        # as busy for the hour the buffer retains it -- and the idle sweep never
        # released it. Agents kill processes exactly when a tool call looks
        # stuck, which is how this compounded: the sandbox that frustrated
        # someone was then the one that could never be reclaimed.
        return any(not _has_stopped(p) for p in processes)

    async def reclaim_orphans(self, *, dry_run: bool = False) -> tuple[str, ...]:
        """Destroy provider objects this environment created and no longer wants.

        Reclaimable means *this database* asked for the object to be gone -- see
        `_reclaim_reason`. An object this database has never heard of is not
        reclaimable at any confidence, and that is the distinction this method
        exists to hold.
        """
        deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
        objects = await self._provider.list_objects(deadline_at=deadline_at)

        reclaimed: list[str] = []
        unattributed: list[str] = []
        for obj in objects:
            if obj.sandbox_id is None:
                # Not identifiable as ours at all. Leaving a stray object
                # running is recoverable; deleting a stranger's container is
                # not.
                continue

            async with self._uow_factory() as uow:
                repository = SandboxRepository(uow)
                sandbox = await repository.get(obj.sandbox_id)
                instance = (
                    await repository.current_instance(obj.sandbox_id)
                    if sandbox is not None
                    else None
                )

            if sandbox is None:
                # Identifiable, but not ours -- and this is the branch that must
                # never destroy.
                #
                # A sandbox this environment created always has a row. Nothing
                # hard-deletes one (`SandboxService.destroy` sets
                # `desired_state=DELETED` and keeps it), and `begin_instance`
                # commits the row *before* the provider is asked to create
                # anything, so even a create whose response was lost leaves a
                # row behind. "No row" therefore cannot mean "ours and
                # forgotten". It can only mean another database's: a second
                # environment sharing this provider account, a developer's local
                # backend, or a database restored from before the object existed.
                #
                # Destroying on it is an unfalsifiable negative that resolves
                # towards deleting a stranger's live workspace, and on a
                # SANDBOX_NATIVE provider that is the user's disk. It is what
                # happened: dev and prod held two API keys for one E2B team,
                # both defaulted to the same metadata namespace, and each
                # environment's sweep destroyed the other's sandboxes every five
                # minutes -- five times inside one twenty-minute conversation,
                # each kill seconds after the other side rebuilt it.
                #
                # The namespace is the boundary that stops the two seeing each
                # other at all, and it is derived rather than shared now
                # (`provider_factory.resolve_metadata_namespace`). This is the
                # second line: a namespace can be misconfigured again, and a
                # report costs money while a destroy costs work nobody can get
                # back.
                unattributed.append(obj.provider_id)
                continue

            reason = self._reclaim_reason(obj, sandbox, instance)
            if reason is None:
                continue

            if dry_run:
                reclaimed.append(obj.name)
                continue
            try:
                await self._provider.destroy(obj.name, deadline_at=deadline_at)
            except Exception as exc:
                logger.warning(
                    "workspace.sandbox_sweeper.orphan_destroy_failed",
                    sandbox_id=str(obj.sandbox_id),
                    error_type=type(exc).__name__,
                )
                continue
            # A destroy that returns is not a destroy that happened. On E2B a
            # paused sandbox is listed like any other but does not go away when
            # killed, so this loop logged eighteen reclaims every five minutes
            # for hours -- the same eighteen ids, eleven times each -- while the
            # account held ninety-nine paused sandboxes and the count never
            # moved. Nothing was wrong with the sweep except that it believed
            # itself. Confirming against the provider is what makes the count
            # in `reclaimed_orphaned_objects.observed` mean anything, and what
            # makes a provider that cannot reap this object say so instead of
            # rediscovering it forever.
            if await self._still_present(obj, deadline_at=deadline_at):
                logger.warning(
                    "workspace.sandbox_sweeper.orphan_destroy_ineffective",
                    sandbox_id=str(obj.sandbox_id),
                    reason=reason,
                )
                continue
            reclaimed.append(obj.name)
            logger.info(
                "workspace.sandbox_sweeper.orphan_reclaimed",
                sandbox_id=str(obj.sandbox_id),
                reason=reason,
            )

        if unattributed:
            # One line per sweep, not one per object. A shared provider account
            # can hold hundreds of these, and a per-object log would bury the
            # signal the report exists to raise: a rising count means another
            # environment has started writing into this account.
            logger.info(
                "workspace.sandbox_sweeper.unattributed_objects",
                count=len(unattributed),
                # Joined, not a tuple: the log pipeline drops any value that is
                # not a scalar, so a tuple arrives as `dropped_fields` and the
                # sample that makes the count actionable is lost.
                sample=",".join(sorted(unattributed)[:_UNATTRIBUTED_SAMPLE]),
            )
        return tuple(reclaimed)

    def _reclaim_reason(self, obj, sandbox, instance) -> str | None:
        """Why this object may be destroyed, or None to leave it alone.

        Split from the loop so the loop is about *doing* a reclaim and this is
        about *deciding* one -- and so neither trips the complexity ratchet,
        which the two together did.

        Note what is absent: "there is no row". That never reaches here, because
        it is not a reason at any confidence. See the caller.
        """
        if sandbox.desired_state is SandboxDesiredState.DELETED:
            if instance is not None and (
                instance.state is SandboxInstanceState.CREATING
            ):
                # A provision is in flight against a row that still reads
                # DELETED. `destroy` sets DELETED, and the `_provision` that
                # follows only sets it back to PRESENT at the very end -- so
                # between `begin_instance` and that write, a live create looks
                # exactly like an abandoned sandbox. Destroying here kills the
                # sandbox the caller is waiting on.
                #
                # CREATING is the whole window and not merely most of it:
                # `mark_instance_ready` and `set_desired_state(PRESENT)` commit
                # in one unit of work, so READY is never observable while the
                # row still says DELETED.
                return None
            return "sandbox deleted"
        if obj.legacy:
            # A pre-cutover object carries no epoch, so it cannot be judged by
            # one. While both provisioning paths exist it may still be the
            # *only* container serving this sandbox, and destroying it would
            # kill a live workspace. It is only reclaimable once this module has
            # provisioned a replacement.
            if instance is None:
                return None
            return "superseded by the current provisioning path"
        if (
            self._epoch_is_a_fence
            and obj.epoch is not None
            and (obj.epoch < sandbox.epoch)
        ):
            return f"epoch {obj.epoch} is behind {sandbox.epoch}"
        return None

    async def _still_present(self, obj, *, deadline_at: datetime) -> bool:
        """Whether the provider can still see *this* object after the destroy.

        Identity, not existence. `inspect` resolves a name to whatever object
        now holds that sandbox id, and reclaiming a superseded epoch leaves the
        current one standing -- so "something is there" would read every
        successful epoch reclaim as a failure. Only the same provider id means
        nothing happened.

        A provider that cannot answer is given the benefit of the doubt: the
        sweep runs again in five minutes, and a false alarm every cycle would
        bury the real signal this exists to raise.
        """
        try:
            instance = await self._provider.inspect(obj.name, deadline_at=deadline_at)
        except ProviderGone:
            return False
        except ProviderFailed, ProviderNotReady, ProviderRejected, SandboxError:
            return False
        return instance is not None and instance.provider_id == obj.provider_id


def _has_stopped(process) -> bool:
    """Whether this process is over, by either signal the providers give.

    Both are needed, and neither alone is enough. E2B records a cancelled
    process with `exit_code=None` (`e2b_output.record_cancelled`), so the exit
    code alone made every process an agent killed pin its sandbox as busy for
    the hour the output buffer retains it -- and agents kill processes exactly
    when a tool call looks stuck, so the sandbox that had just frustrated
    someone was then the one the idle sweep could never reclaim. The state
    alone is not enough either: `ProcessDescriptor.state` is typed `object`,
    and a provider that reports a state this module does not know would read as
    running forever.
    """
    if process.exit_code is not None:
        return True
    return process.state in TERMINAL_PROCESS_STATES or str(
        getattr(process.state, "value", process.state)
    ) in {state.value for state in TERMINAL_PROCESS_STATES}

"""Reconcile provider inventory against durable state.

Everything else in AgentBox reasons forwards from a durable record: an
allocation exists, therefore a sandbox should. This module reasons backwards,
asking the provider what is actually running, because two failure modes are
invisible from the durable side:

- a sandbox we already decided to destroy that is still running, because the
  destroy failed or a manager died mid-cleanup. Nothing retries it, and it
  bills until someone notices by hand;
- a sandbox the provider stopped on its own. Durable state still reads ACTIVE
  with an unchanged epoch, so runtime routing keeps handing out handles to
  processes and interpreters that the pause already destroyed.

It reclaims **only sandboxes this deployment created and has already given up
on**. A provider account is routinely shared between environments, so anything
this database does not recognise belongs to somebody else and is left strictly
alone. The database is the reliable record here - rows do not vanish on their
own - so an unrecognised sandbox means "not ours", never "abandoned". Getting
that backwards would delete another environment's live sandboxes, which is far
worse than leaving stray compute for a human to find.

The create-attempt token is what makes ownership decidable: it is written
before the provider is ever called and stamped into provider metadata, so it
ties a running object back to the exact row that authorised it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from agentbox.domain import AllocationState, SandboxKey, WorkloadKind
from agentbox.observability import get_logger
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderInventoryAllocation,
    ProviderLifecycleError,
    ProviderMetadataEntry,
    ProviderRateLimited,
    SandboxProviderPort,
)


logger = get_logger(__name__)

# Allocation states whose provider object is expected to exist. RELEASED is
# included deliberately: a released workspace is paused, not gone, and its
# filesystem is the user's data until retention expiry destroys it.
_EXPECTED_PRESENT = frozenset(
    {
        AllocationState.RESERVED.value,
        AllocationState.PROVISIONING.value,
        AllocationState.UNKNOWN.value,
        AllocationState.ACTIVE.value,
        AllocationState.QUIESCING.value,
        AllocationState.DRAINING.value,
        AllocationState.RELEASED.value,
    }
)


class SandboxInventorySweeper:
    def __init__(
        self,
        database: StateDatabase,
        provider: SandboxProviderPort,
        *,
        untracked_grace_seconds: float = 900,
    ) -> None:
        self._database = database
        self._provider = provider
        self._untracked_grace = timedelta(seconds=untracked_grace_seconds)
        self._first_seen_untracked: dict[str, datetime] = {}

    async def sweep_once(self, *, deadline_at: datetime) -> int:
        inventory = await self._inventory(deadline_at=deadline_at)
        if not inventory:
            self._first_seen_untracked.clear()
            return 0

        tokens = tuple(
            {item.allocation_token for item in inventory if item.allocation_token}
        )
        async with self._database.uow() as uow:
            states = await uow.repository.classify_inventory_tokens(tokens)
            await uow.commit()

        acted = 0
        seen: set[str] = set()
        for item in inventory:
            if datetime.now(timezone.utc) >= deadline_at:
                break
            seen.add(item.provider_id)
            if await self._handle(item, states, deadline_at=deadline_at):
                acted += 1
        # Forget anything that is no longer in inventory, so a provider object
        # that comes back later starts its grace period again rather than being
        # destroyed on sight.
        for provider_id in set(self._first_seen_untracked) - seen:
            self._first_seen_untracked.pop(provider_id, None)
        return acted

    async def _inventory(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderInventoryAllocation, ...]:
        try:
            return await self._provider.find_allocations(
                (
                    ProviderMetadataEntry("managed-by", "agentbox"),
                    ProviderMetadataEntry("provider-scope", self._provider.scope),
                ),
                deadline_at=deadline_at,
            )
        except (ProviderRateLimited, ProviderLifecycleError) as exc:
            # Never guess when the provider will not answer. Sweeping on a
            # partial or failed listing could destroy live sandboxes.
            logger.warning(
                "agentbox.inventory.listing_failed",
                provider_scope=self._provider.scope,
                error_type=type(exc).__name__,
            )
            return ()

    async def _handle(
        self,
        item: ProviderInventoryAllocation,
        states: dict[UUID, str | None],
        *,
        deadline_at: datetime,
    ) -> bool:
        if item.allocation_token is None:
            # We stamp a token on everything we create, so an object without
            # one was created by something else - another deployment sharing
            # this provider account, or a release predating the token. Not ours
            # to delete.
            return False

        state = states.get(item.allocation_token)
        if state is None:
            # The token is real but this database has never heard of it, which
            # means another deployment created it. Provider scope is supposed
            # to separate dev from prod, but scopes get copied and reused, and
            # the database is the reliable record - a row does not vanish on
            # its own. So an unrecognised token means "not ours", never
            # "abandoned". Destroying here would delete another environment's
            # live sandboxes.
            logger.info(
                "agentbox.inventory.unrecognised_sandbox_ignored",
                provider_scope=self._provider.scope,
                provider_id=item.provider_id,
            )
            return False

        if state in _EXPECTED_PRESENT:
            self._first_seen_untracked.pop(item.provider_id, None)
            if state == AllocationState.ACTIVE.value and item.running is False:
                return await self._record_provider_pause(item)
            return False

        # Ours, and durable state already says it should be gone: a destroy
        # that failed, or a manager that died mid-cleanup. This is the only
        # case where reclaiming is unambiguously correct.
        if not self._grace_elapsed(item.provider_id):
            return False
        return await self._destroy_untracked(item, state, deadline_at=deadline_at)

    def _grace_elapsed(self, provider_id: str) -> bool:
        """Require an object to look reclaimable twice, spaced apart, first.

        An allocation can reach a terminal state moments before its own destroy
        completes, so acting on first sight would race the normal cleanup path
        for something that is already being handled.
        """

        now = datetime.now(timezone.utc)
        first_seen = self._first_seen_untracked.setdefault(provider_id, now)
        return now - first_seen >= self._untracked_grace

    async def _record_provider_pause(
        self, item: ProviderInventoryAllocation
    ) -> bool:
        async with self._database.uow() as uow:
            allocation = await uow.repository.get_allocation_by_token(
                item.allocation_token
            )
            if allocation is None:
                await uow.commit()
                return False
            changed = (
                await uow.repository.mark_allocation_released_after_provider_pause(
                    allocation.allocation_id
                )
            )
            await uow.commit()
        if changed:
            logger.warning(
                "agentbox.inventory.provider_paused_active_allocation",
                provider_scope=self._provider.scope,
                provider_id=item.provider_id,
                workload_kind=allocation.key.workload_kind.value,
            )
        return changed

    async def _destroy_untracked(
        self,
        item: ProviderInventoryAllocation,
        state: str | None,
        *,
        deadline_at: datetime,
    ) -> bool:
        # The allocation is terminal, so its identity is gone; the exact
        # provider ID is what remains, and it is all the provider needs to
        # destroy precisely this one object.
        ref = ProviderAllocationRef(
            provider_id=item.provider_id,
            provider_instance_id=item.provider_instance_id,
            allocation_id=uuid_or_zero(item.allocation_token),
            allocation_token=uuid_or_zero(item.allocation_token),
            key=_UNKNOWN_KEY,
        )
        try:
            await self._provider.destroy_allocation(ref, deadline_at=deadline_at)
        except ProviderLifecycleError as exc:
            logger.warning(
                "agentbox.inventory.untracked_destroy_failed",
                provider_scope=self._provider.scope,
                provider_id=item.provider_id,
                error_type=type(exc).__name__,
            )
            return False
        self._first_seen_untracked.pop(item.provider_id, None)
        logger.warning(
            "agentbox.inventory.untracked_sandbox_destroyed",
            provider_scope=self._provider.scope,
            provider_id=item.provider_id,
            allocation_state=state,
        )
        return True


def uuid_or_zero(value: UUID | None) -> UUID:
    return value if value is not None else UUID(int=0)


# Untracked objects have no logical identity left to report. The kind is only
# used for provider-side logging and never for a lifecycle decision.
_UNKNOWN_KEY = SandboxKey(
    workload_kind=WorkloadKind.FUNCTION,
    logical_id=UUID(int=0),
)


async def inventory_sweep_loop(
    sweeper: SandboxInventorySweeper,
    *,
    interval_seconds: float,
    operation_timeout_seconds: float,
) -> None:
    while True:
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=operation_timeout_seconds
        )
        try:
            await sweeper.sweep_once(deadline_at=deadline)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("agentbox.inventory.sweep_failed", exc_info=True)
        await asyncio.sleep(interval_seconds)

"""Reconcile provider inventory against durable state.

Everything else in AgentBox reasons forwards from a durable record: an
allocation exists, therefore a sandbox should. This module reasons backwards.
It asks the provider what is actually running and charges anything it finds
against durable state, because the two failure modes that cost real money are
both invisible from the durable side:

- a sandbox nobody owns. Its allocation row is gone - a restored database, a
  renamed provider scope, a deleted row - so no code path will ever destroy it
  and it bills until someone notices by hand;
- a sandbox the provider stopped on its own. Durable state still reads ACTIVE
  with an unchanged epoch, so runtime routing keeps handing out handles to
  processes and interpreters that the pause already destroyed.

The create-attempt token makes this exact rather than heuristic: it is written
before the provider is ever called and stamped into provider metadata, so any
object carrying a token AgentBox cannot account for is genuinely unowned.
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
        state = states.get(item.allocation_token) if item.allocation_token else None

        if state in _EXPECTED_PRESENT:
            self._first_seen_untracked.pop(item.provider_id, None)
            if state == AllocationState.ACTIVE.value and item.running is False:
                return await self._record_provider_pause(item)
            return False

        # Either the token is unknown to us, or its allocation is terminal and
        # this object should already be gone. Both are unowned compute.
        if not self._grace_elapsed(item.provider_id):
            return False
        return await self._destroy_untracked(item, state, deadline_at=deadline_at)

    def _grace_elapsed(self, provider_id: str) -> bool:
        """Require an object to look unowned twice, spaced apart, before acting.

        A token is committed before the provider is called, so an in-flight
        create should never look unowned. The grace period is insurance against
        the cases that would make that reasoning wrong - clock skew, a replica
        lagging, a scope briefly misconfigured - because the cost of being
        wrong is destroying a live user's sandbox.
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
        # An untracked object has no allocation to reference, so the exact
        # provider ID is all the identity available - which is also all the
        # provider needs to destroy precisely this one thing.
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

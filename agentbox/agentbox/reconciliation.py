from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agentbox.domain import (
    AgentBoxError,
    CreateReconcileCandidate,
    DispatchState,
    ErrorCode,
    PhysicalAllocation,
    WorkloadKind,
)
from agentbox.lifecycle import allocation_metadata
from agentbox.observability import get_logger
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderLifecycleError,
    ProviderNotReady,
    ProviderRateLimited,
    SandboxProviderPort,
)


logger = get_logger(__name__)


class AgentBoxReconciler:
    """Repair ambiguous provider transitions by exact durable identity."""

    def __init__(
        self,
        database: StateDatabase,
        provider: SandboxProviderPort,
        *,
        reserved_create_stale_seconds: float = 30,
        dispatched_create_stale_seconds: float = 15 * 60,
        claim_seconds: float = 30,
        retry_seconds: float = 1,
        batch_size: int = 100,
    ) -> None:
        self._database = database
        self._provider = provider
        self._reserved_create_stale = timedelta(
            seconds=reserved_create_stale_seconds
        )
        self._dispatched_create_stale = timedelta(
            seconds=dispatched_create_stale_seconds
        )
        self._claim_seconds = claim_seconds
        self._retry_seconds = retry_seconds
        self._batch_size = batch_size

    async def reconcile_once(self, *, deadline_at: datetime) -> int:
        now = datetime.now(timezone.utc)
        async with self._database.uow() as uow:
            repaired = await uow.repository.repair_terminal_admission_invariants(
                self._provider.scope,
                now=now,
            )
            candidates = await uow.repository.list_due_create_reconciliation(
                self._provider.scope,
                stale_reserved_before=now - self._reserved_create_stale,
                stale_dispatched_before=now - self._dispatched_create_stale,
                now=now,
                limit=self._batch_size,
            )
            draining = await uow.repository.list_draining_allocations(
                self._provider.scope,
                limit=self._batch_size,
            )
            await uow.commit()
        if repaired:
            logger.warning(
                "agentbox.admission.invariant_repaired",
                provider_scope=self._provider.scope,
                allocation_count=repaired,
            )
        reconciled = 0
        for candidate in candidates:
            if datetime.now(timezone.utc) >= deadline_at:
                break
            claimed_until = min(
                deadline_at,
                datetime.now(timezone.utc) + timedelta(seconds=self._claim_seconds),
            )
            async with self._database.uow() as uow:
                claimed = await uow.repository.claim_create_reconciliation(
                    candidate.allocation.allocation_token,
                    claimed_until=claimed_until,
                    stale_reserved_before=(
                        datetime.now(timezone.utc) - self._reserved_create_stale
                    ),
                    stale_dispatched_before=(
                        datetime.now(timezone.utc) - self._dispatched_create_stale
                    ),
                )
                await uow.commit()
            if not claimed:
                continue
            await self._reconcile_create(candidate, deadline_at=deadline_at)
            reconciled += 1
        for allocation in draining:
            if datetime.now(timezone.utc) >= deadline_at:
                break
            if await self._finalize_draining(
                allocation,
                deadline_at=deadline_at,
            ):
                reconciled += 1
        return reconciled

    async def _finalize_draining(
        self,
        allocation: PhysicalAllocation,
        *,
        deadline_at: datetime,
    ) -> bool:
        if allocation.provider_id is None:
            return False
        try:
            await self._provider.destroy_allocation(
                self._provider_ref(allocation),
                deadline_at=deadline_at,
            )
        except ProviderLifecycleError:
            return False
        async with self._database.uow() as uow:
            finalized = await uow.repository.complete_draining_allocation(
                allocation.allocation_id,
                expected_resource_generation=allocation.resource_generation,
            )
            await uow.commit()
        return finalized

    async def _reconcile_create(
        self,
        candidate: CreateReconcileCandidate,
        *,
        deadline_at: datetime,
    ) -> None:
        allocation = candidate.allocation
        if candidate.dispatch_state == DispatchState.RESERVED:
            # No provider call occurred. A manager died between durable
            # admission and dispatch, so this reservation is safe to release.
            async with self._database.uow() as uow:
                await uow.repository.mark_create_failed(
                    allocation.allocation_token,
                    error_code=ErrorCode.DEADLINE_EXCEEDED.value,
                    expected_resource_generation=allocation.resource_generation,
                )
                await uow.commit()
            return
        if candidate.dispatch_state in {
            DispatchState.DISPATCHED,
            DispatchState.UNKNOWN,
        }:
            try:
                matches = await self._provider.find_allocations(
                    allocation_metadata(
                        self._provider.scope,
                        allocation.allocation_id,
                        allocation.allocation_token,
                        allocation.key,
                        allocation.profile,
                    ),
                    deadline_at=deadline_at,
                )
            except ProviderRateLimited as exc:
                retry_at = datetime.now(timezone.utc) + timedelta(
                    milliseconds=exc.retry_after_ms
                )
                async with self._database.uow() as uow:
                    await uow.repository.block_provider_creates(
                        self._provider.scope, blocked_until=retry_at
                    )
                    await uow.repository.defer_create_reconciliation(
                        allocation.allocation_token, reconcile_after=retry_at
                    )
                    await uow.commit()
                return
            except ProviderLifecycleError:
                await self._defer(allocation)
                return

            if len(matches) == 0:
                # Inventory is not a linearizable proof of absence. Resolving an
                # ambiguous create from an empty list can orphan a sandbox that
                # appears later, so retain the exact token for reconciliation.
                await self._defer(allocation)
                return
            if len(matches) != 1:
                # Multiple resources for one allocation token violate the
                # provider contract. Never guess which resource owns user state.
                await self._defer(allocation)
                return

            match = matches[0]
            if (
                allocation.key.workload_kind == WorkloadKind.WORKSPACE
                and match.workspace_storage is None
            ):
                await self._destroy_failed_allocation(
                    allocation,
                    provider_id=match.provider_id,
                    provider_instance_id=match.provider_instance_id,
                    deadline_at=deadline_at,
                )
                return
            discard_conflicting_native_workspace = False
            try:
                async with self._database.uow() as uow:
                    allocation = await uow.repository.acknowledge_create(
                        allocation.allocation_token,
                        provider_id=match.provider_id,
                        expected_resource_generation=(
                            allocation.resource_generation
                        ),
                        provider_instance_id=match.provider_instance_id,
                    )
                    if match.workspace_storage is not None:
                        try:
                            await uow.repository.bind_workspace_storage(
                                allocation.key,
                                provider_storage_id=(
                                    match.workspace_storage.provider_storage_id
                                ),
                                allocation_id=(
                                    allocation.allocation_id
                                    if match.workspace_storage.bound_to_allocation
                                    else None
                                ),
                            )
                        except AgentBoxError as exc:
                            if (
                                exc.code == ErrorCode.OPERATION_CONFLICT
                                and match.workspace_storage.bound_to_allocation
                                and allocation.key.workload_kind
                                == WorkloadKind.WORKSPACE
                            ):
                                # A newer sandbox-native workspace already owns
                                # the logical workspace. Preserve it and discard
                                # only this stale exact provider match.
                                discard_conflicting_native_workspace = True
                                await uow.rollback()
                            else:
                                raise
                    if not discard_conflicting_native_workspace:
                        await uow.commit()
            except AgentBoxError as exc:
                if exc.code != ErrorCode.ALLOCATION_CHANGED:
                    raise
                discard_conflicting_native_workspace = True
            if discard_conflicting_native_workspace:
                await self._destroy_failed_allocation(
                    candidate.allocation,
                    provider_id=match.provider_id,
                    provider_instance_id=match.provider_instance_id,
                    deadline_at=deadline_at,
                )
                return

        if allocation.provider_id is None:
            await self._defer(allocation)
            return
        provider_ref = self._provider_ref(allocation)
        try:
            ready = await self._provider.wait_ready(
                provider_ref,
                profile=allocation.profile,
                deadline_at=deadline_at,
            )
        except ProviderNotReady as exc:
            retry_at = datetime.now(timezone.utc) + timedelta(
                milliseconds=exc.retry_after_ms
            )
            async with self._database.uow() as uow:
                await uow.repository.mark_allocation_provisioning_retry(
                    allocation.allocation_id,
                    retry_after=retry_at,
                    error_code=ErrorCode.PROVISIONING.value,
                    expected_resource_generation=allocation.resource_generation,
                )
                await uow.commit()
            return
        except ProviderAllocationFailed:
            await self._destroy_failed_allocation(
                allocation,
                provider_id=allocation.provider_id,
                provider_instance_id=allocation.provider_instance_id,
                deadline_at=deadline_at,
            )
            return
        if ready.provider_id != allocation.provider_id:
            await self._defer(allocation)
            return
        try:
            async with self._database.uow() as uow:
                await uow.repository.acknowledge_create(
                    allocation.allocation_token,
                    provider_id=ready.provider_id,
                    expected_resource_generation=allocation.resource_generation,
                    provider_instance_id=ready.provider_instance_id,
                )
                await uow.repository.publish_allocation(
                    allocation.allocation_token,
                    expected_resource_generation=allocation.resource_generation,
                )
                await uow.commit()
        except AgentBoxError as exc:
            if exc.code != ErrorCode.ALLOCATION_CHANGED:
                raise
            await self._destroy_failed_allocation(
                allocation,
                provider_id=ready.provider_id,
                provider_instance_id=ready.provider_instance_id,
                deadline_at=deadline_at,
            )

    async def _destroy_failed_allocation(
        self,
        allocation: PhysicalAllocation,
        *,
        provider_id: str,
        provider_instance_id: str | None,
        deadline_at: datetime,
    ) -> None:
        if not await self._owns_allocation_generation(allocation):
            return
        try:
            await self._provider.destroy_allocation(
                ProviderAllocationRef(
                    provider_id=provider_id,
                    provider_instance_id=provider_instance_id,
                    allocation_id=allocation.allocation_id,
                    allocation_token=allocation.allocation_token,
                    key=allocation.key,
                ),
                deadline_at=deadline_at,
            )
        except ProviderLifecycleError:
            await self._defer(allocation)
            return
        async with self._database.uow() as uow:
            await uow.repository.mark_create_failed(
                allocation.allocation_token,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                expected_resource_generation=allocation.resource_generation,
            )
            await uow.commit()

    async def _defer(self, allocation: PhysicalAllocation) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=self._retry_seconds)
        async with self._database.uow() as uow:
            await uow.repository.defer_create_reconciliation(
                allocation.allocation_token,
                reconcile_after=retry_at,
                expected_resource_generation=allocation.resource_generation,
            )
            await uow.commit()

    async def _owns_allocation_generation(
        self, allocation: PhysicalAllocation
    ) -> bool:
        async with self._database.uow() as uow:
            current = await uow.repository.get_allocation_by_token(
                allocation.allocation_token
            )
            await uow.commit()
        return (
            current is not None
            and current.resource_generation == allocation.resource_generation
        )

    @staticmethod
    def _provider_ref(allocation: PhysicalAllocation) -> ProviderAllocationRef:
        if allocation.provider_id is None:
            raise RuntimeError("reconciled allocation has no provider identity")
        return ProviderAllocationRef(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
            allocation_id=allocation.allocation_id,
            allocation_token=allocation.allocation_token,
            key=allocation.key,
            resource_generation=allocation.resource_generation,
        )


async def reconciliation_loop(
    reconciler: AgentBoxReconciler,
    *,
    interval_seconds: float,
    operation_timeout_seconds: float,
) -> None:
    while True:
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=operation_timeout_seconds
        )
        try:
            await reconciler.reconcile_once(deadline_at=deadline)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The next bounded pass retries durable candidates. Request handling
            # remains independent of provider inventory availability.
            logger.warning(
                "agentbox.reconcile.failed",
                error_type=type(exc).__name__,
                exc_info=True,
            )
        await asyncio.sleep(interval_seconds)

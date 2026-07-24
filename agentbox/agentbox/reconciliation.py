from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agentbox.domain import (
    CreateReconcileCandidate,
    DispatchState,
    ErrorCode,
    PhysicalAllocation,
    WorkloadKind,
)
from agentbox.lifecycle import allocation_metadata
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderLifecycleError,
    ProviderNotReady,
    ProviderRateLimited,
    SandboxProviderPort,
)
from agentbox.telemetry import observed_control_operation


class AgentBoxReconciler:
    """Repair ambiguous provider transitions by exact durable identity."""

    def __init__(
        self,
        database: StateDatabase,
        provider: SandboxProviderPort,
        *,
        create_absence_grace_seconds: float = 30,
        claim_seconds: float = 30,
        retry_seconds: float = 1,
        batch_size: int = 100,
    ) -> None:
        self._database = database
        self._provider = provider
        self._absence_grace = timedelta(seconds=create_absence_grace_seconds)
        self._claim_seconds = claim_seconds
        self._retry_seconds = retry_seconds
        self._batch_size = batch_size

    @observed_control_operation("reconcile")
    async def reconcile_once(self, *, deadline_at: datetime) -> int:
        now = datetime.now(timezone.utc)
        async with self._database.uow() as uow:
            candidates = await uow.repository.list_due_create_reconciliation(
                self._provider.scope,
                now=now,
                limit=self._batch_size,
            )
            await uow.commit()
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
                )
                await uow.commit()
            if not claimed:
                continue
            await self._reconcile_create(candidate, deadline_at=deadline_at)
            reconciled += 1
        return reconciled

    async def _reconcile_create(
        self,
        candidate: CreateReconcileCandidate,
        *,
        deadline_at: datetime,
    ) -> None:
        allocation = candidate.allocation
        if candidate.dispatch_state == DispatchState.UNKNOWN:
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
                if (
                    datetime.now(timezone.utc) - candidate.dispatch_started_at
                    < self._absence_grace
                ):
                    await self._defer(allocation)
                    return
                async with self._database.uow() as uow:
                    await uow.repository.mark_create_failed(
                        allocation.allocation_token,
                        error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                    )
                    await uow.commit()
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
            async with self._database.uow() as uow:
                allocation = await uow.repository.acknowledge_create(
                    allocation.allocation_token,
                    provider_id=match.provider_id,
                    provider_instance_id=match.provider_instance_id,
                )
                if match.workspace_storage is not None:
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
                await uow.commit()

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
        async with self._database.uow() as uow:
            await uow.repository.acknowledge_create(
                allocation.allocation_token,
                provider_id=ready.provider_id,
                provider_instance_id=ready.provider_instance_id,
            )
            await uow.repository.publish_allocation(allocation.allocation_token)
            await uow.commit()

    async def _destroy_failed_allocation(
        self,
        allocation: PhysicalAllocation,
        *,
        provider_id: str,
        provider_instance_id: str | None,
        deadline_at: datetime,
    ) -> None:
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
            )
            await uow.commit()

    async def _defer(self, allocation: PhysicalAllocation) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=self._retry_seconds)
        async with self._database.uow() as uow:
            await uow.repository.defer_create_reconciliation(
                allocation.allocation_token, reconcile_after=retry_at
            )
            await uow.commit()

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
        except Exception:
            # The next bounded pass retries durable candidates. Request handling
            # remains independent of provider inventory availability.
            pass
        await asyncio.sleep(interval_seconds)

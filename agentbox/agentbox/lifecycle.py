from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    AllocationState,
    CapacityErrorContext,
    ErrorCode,
    LogicalSandbox,
    PhysicalAllocation,
    ProviderAdmissionDecision,
    ProviderAdmissionPolicy,
    ProviderErrorContext,
    RetryDisposition,
    SandboxHandle,
    SandboxKey,
    SandboxMaintenanceClaim,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.observability import create_inherited_task
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRejected,
    ProviderCreateRequest,
    ProviderMetadataEntry,
    ProviderNotReady,
    ProviderLifecycleError,
    ProviderRateLimited,
    ProviderStorageRequest,
    SandboxProviderPort,
)
from agentbox.persistence.uow import StateDatabase


def allocation_metadata(
    provider_scope: str,
    allocation_id: UUID,
    allocation_token: UUID,
    key: SandboxKey,
    profile: SandboxProfileRef,
) -> tuple[ProviderMetadataEntry, ...]:
    return (
        ProviderMetadataEntry("managed-by", "agentbox"),
        ProviderMetadataEntry("provider-scope", provider_scope),
        ProviderMetadataEntry("workload-kind", key.workload_kind.value),
        ProviderMetadataEntry("logical-id", str(key.logical_id)),
        ProviderMetadataEntry("allocation-id", str(allocation_id)),
        ProviderMetadataEntry("allocation-token", str(allocation_token)),
        ProviderMetadataEntry("profile-name", profile.name),
        ProviderMetadataEntry("profile-digest", profile.digest),
    )


class SandboxLifecycleService:
    """Orchestrate state and provider calls without holding DB I/O scopes open."""

    def __init__(
        self,
        database: StateDatabase,
        provider: SandboxProviderPort,
        admission_policy: ProviderAdmissionPolicy | None = None,
        *,
        workspace_retention_seconds: float = 48 * 60 * 60,
    ) -> None:
        self._database = database
        self._provider = provider
        self._admission_policy = (
            admission_policy or ProviderAdmissionPolicy.permissive_for_tests()
        )
        self._workspace_retention_seconds = workspace_retention_seconds

    async def ensure(
        self,
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        admission_class: AdmissionClass,
        deadline_at: datetime,
        verify_ready: bool = False,
    ) -> SandboxHandle:
        self._check_deadline(deadline_at)
        request_hash = self._create_request_hash(
            key,
            profile,
            provider_name=self._provider.name,
            provider_scope=self._provider.scope,
            admission_class=admission_class,
        )

        # Transaction 1: durable logical resource and one allocation intent.
        decision: ProviderAdmissionDecision
        pending_retry = False
        intent = None
        resumed = None
        storage = None
        superseded_native_allocation = None
        try:
            async with self._database.uow() as uow:
                logical = await uow.repository.ensure_logical(key, profile)
                if key.workload_kind == WorkloadKind.WORKSPACE:
                    storage = await uow.repository.ensure_workspace_storage(
                        key,
                        provider_name=self._provider.name,
                        storage_kind=self._provider.workspace_storage_kind,
                    )
                resumed = await uow.repository.resume_released_allocation(key, profile)
                if resumed is None:
                    intent = await uow.repository.begin_allocation(
                        key,
                        profile,
                        provider_name=self._provider.name,
                        provider_scope=self._provider.scope,
                        admission_class=admission_class.value,
                        request_hash=request_hash,
                    )
                allocation_for_admission = resumed or (
                    intent.allocation if intent is not None else None
                )
                if allocation_for_admission is None:  # pragma: no cover
                    raise RuntimeError("ensure produced no allocation for admission")
                if (
                    storage is not None
                    and storage.storage_kind == StorageKind.SANDBOX_NATIVE
                    and storage.bound_allocation_id is not None
                    and storage.bound_allocation_id
                    != allocation_for_admission.allocation_id
                ):
                    bound_allocation = await uow.repository.get_allocation_by_id(
                        storage.bound_allocation_id
                    )
                    if (
                        bound_allocation is not None
                        and bound_allocation.state
                        in {
                            AllocationState.ACTIVE,
                            AllocationState.DRAINING,
                            AllocationState.RELEASED,
                        }
                    ):
                        superseded_native_allocation = bound_allocation
                        if bound_allocation.state == AllocationState.ACTIVE:
                            await uow.repository.mark_allocation_draining(
                                bound_allocation.allocation_id
                            )
                pending_retry = (
                    intent is not None
                    and intent.allocation.state == AllocationState.RESERVED
                    and intent.allocation.retry_after is not None
                    and intent.allocation.retry_after > datetime.now(timezone.utc)
                )
                if pending_retry:
                    decision = ProviderAdmissionDecision(
                        accepted=True,
                        active=0,
                        reserved=0,
                        limit=self._admission_policy.max_active,
                    )
                else:
                    decision = await uow.repository.reserve_provider_capacity(
                        allocation_for_admission.allocation_id,
                        admission_class=admission_class,
                        policy=self._admission_policy,
                    )
                await uow.commit()
        except asyncio.CancelledError:
            if intent is not None and intent.should_dispatch_create:
                await self._mark_create_failed_durably(
                    intent.allocation.allocation_token,
                    error_code=ErrorCode.DEADLINE_EXCEEDED,
                )
            raise

        if not decision.accepted:
            raise self._admission_error(decision)

        if superseded_native_allocation is not None:
            await self._retire_superseded_native_workspace(
                superseded_native_allocation,
                deadline_at=deadline_at,
            )

        if resumed is not None:
            return await self._wait_and_publish(
                logical,
                resumed,
                profile=profile,
                deadline_at=deadline_at,
            )
        if intent is None:  # pragma: no cover - branch invariant
            raise RuntimeError("allocation intent was not created")

        if intent.allocation.state == AllocationState.ACTIVE:
            if verify_ready:
                return await self._wait_and_publish(
                    intent.logical,
                    intent.allocation,
                    profile=profile,
                    deadline_at=deadline_at,
                )
            return self._handle(intent.logical, intent.allocation)
        if pending_retry:
            return self._handle(intent.logical, intent.allocation)

        # Transaction 2: transition RESERVED -> DISPATCHED exactly once. The
        # provider has not been called yet and this transaction is closed before it is.
        try:
            self._check_deadline(deadline_at)
            async with self._database.uow() as uow:
                dispatch = await uow.repository.mark_create_dispatched(
                    intent.allocation.allocation_token
                )
                await uow.commit()
        except AgentBoxError as exc:
            if exc.code != ErrorCode.DEADLINE_EXCEEDED:
                raise
            await self._mark_create_failed_durably(
                intent.allocation.allocation_token,
                error_code=ErrorCode.DEADLINE_EXCEEDED,
            )
            raise
        except asyncio.CancelledError:
            # The provider has not been called yet. Regardless of whether the
            # dispatch transaction committed before cancellation arrived, this
            # create is definitively unaccepted and can release its reservation.
            await self._mark_create_failed_durably(
                intent.allocation.allocation_token,
                error_code=ErrorCode.DEADLINE_EXCEEDED,
            )
            raise

        if not dispatch:
            allocation = await self._get_allocation(intent.allocation.allocation_token)
            if (
                allocation.state == AllocationState.PROVISIONING
                and allocation.provider_id is not None
            ):
                return await self._wait_and_publish(
                    intent.logical,
                    allocation,
                    profile=profile,
                    deadline_at=deadline_at,
                )
            return self._handle(intent.logical, allocation)

        create_request = ProviderCreateRequest(
            allocation_id=intent.allocation.allocation_id,
            allocation_token=intent.allocation.allocation_token,
            key=key,
            profile=profile,
            deadline_at=deadline_at,
            metadata=allocation_metadata(
                self._provider.scope,
                intent.allocation.allocation_id,
                intent.allocation.allocation_token,
                key,
                profile,
            ),
            workspace_storage=(
                ProviderStorageRequest(
                    storage_kind=storage.storage_kind,
                    storage_token=storage.storage_token,
                    provider_storage_id=storage.provider_storage_id,
                )
                if storage is not None
                else None
            ),
        )

        # External create I/O: no SQLAlchemy unit of work/session exists here.
        try:
            self._check_deadline(deadline_at)
            created = await self._provider.create(create_request)
        except AgentBoxError as exc:
            if exc.code != ErrorCode.DEADLINE_EXCEEDED:
                raise
            async with self._database.uow() as uow:
                await uow.repository.mark_create_failed(
                    intent.allocation.allocation_token,
                    error_code=ErrorCode.DEADLINE_EXCEEDED.value,
                )
                await uow.commit()
            raise
        except asyncio.CancelledError:
            # Cancellation may race a provider accepting the create. Preserve
            # that ambiguity durably so exact metadata reconciliation can
            # adopt or remove the allocation without replaying the create.
            await self._mark_create_unknown_durably(
                intent.allocation.allocation_token
            )
            raise
        except ProviderCreateAmbiguous as exc:
            async with self._database.uow() as uow:
                await uow.repository.mark_create_unknown(
                    intent.allocation.allocation_token,
                    reconcile_after=datetime.now(timezone.utc) + timedelta(seconds=1),
                    error_code=ErrorCode.AMBIGUOUS_CREATE.value,
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.AMBIGUOUS_CREATE,
                "provider create outcome is unknown and will be reconciled",
                retry=RetryDisposition.WAIT,
                status_code=202,
                retry_after_ms=1000,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        except ProviderCreateRejected as exc:
            async with self._database.uow() as uow:
                await uow.repository.mark_create_failed(
                    intent.allocation.allocation_token,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider rejected sandbox creation",
                retry=RetryDisposition.SAFE_SAME_OPERATION,
                status_code=503,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        except ProviderRateLimited as exc:
            blocked_until = datetime.now(timezone.utc) + timedelta(
                milliseconds=exc.retry_after_ms
            )
            async with self._database.uow() as uow:
                await uow.repository.reset_create_after_rejection(
                    intent.allocation.allocation_token,
                    retry_after=blocked_until,
                    error_code=ErrorCode.RATE_LIMITED.value,
                )
                await uow.repository.block_provider_creates(
                    self._provider.scope,
                    blocked_until=blocked_until,
                )
                await uow.commit()
            raise AgentBoxError(
                ErrorCode.RATE_LIMITED,
                "provider admission is rate limited",
                retry=RetryDisposition.WAIT,
                status_code=429,
                retry_after_ms=exc.retry_after_ms,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc

        contract_error: AgentBoxError | None = None
        if key.workload_kind == WorkloadKind.WORKSPACE:
            if created.workspace_storage is None:
                contract_error = AgentBoxError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "workspace provider did not return durable storage identity",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=502,
                    context=ProviderErrorContext(
                        kind="provider", provider_name=self._provider.name
                    ),
                )
        elif created.workspace_storage is not None:
            contract_error = AgentBoxError(
                ErrorCode.INTERNAL,
                "function provider unexpectedly returned persistent storage",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=500,
            )
        # Transaction 3: persist provider acceptance before any readiness I/O.
        try:
            async with self._database.uow() as uow:
                allocation = await uow.repository.acknowledge_create(
                    intent.allocation.allocation_token,
                    provider_id=created.provider_id,
                    provider_instance_id=created.provider_instance_id,
                    provider_request_id=created.provider_request_id,
                )
                if created.workspace_storage is not None:
                    await uow.repository.bind_workspace_storage(
                        key,
                        provider_storage_id=(
                            created.workspace_storage.provider_storage_id
                        ),
                        allocation_id=(
                            intent.allocation.allocation_id
                            if created.workspace_storage.bound_to_allocation
                            else None
                        ),
                    )
                if contract_error is not None:
                    await uow.repository.mark_create_failed(
                        intent.allocation.allocation_token,
                        error_code=contract_error.code.value,
                    )
                await uow.commit()
        except AgentBoxError as exc:
            if (
                exc.code != ErrorCode.OPERATION_CONFLICT
                or created.workspace_storage is None
            ):
                raise
            cleanup_pending = False
            try:
                await self._provider.destroy_allocation(
                    ProviderAllocationRef(
                        provider_id=created.provider_id,
                        provider_instance_id=created.provider_instance_id,
                        allocation_id=intent.allocation.allocation_id,
                        allocation_token=intent.allocation.allocation_token,
                        key=key,
                    ),
                    deadline_at=deadline_at,
                )
            except ProviderLifecycleError:
                cleanup_pending = True
                await self._mark_create_unknown_durably(
                    intent.allocation.allocation_token
                )
            else:
                await self._mark_create_failed_durably(
                    intent.allocation.allocation_token,
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                )
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                (
                    "workspace storage reconciliation is pending"
                    if cleanup_pending
                    else "stale workspace storage binding was fenced"
                ),
                retry=(
                    RetryDisposition.WAIT
                    if cleanup_pending
                    else RetryDisposition.SAFE_SAME_OPERATION
                ),
                status_code=503,
                retry_after_ms=1000 if cleanup_pending else None,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        if contract_error is not None:
            raise contract_error

        return await self._wait_and_publish(
            intent.logical,
            allocation,
            profile=profile,
            deadline_at=deadline_at,
        )

    async def _wait_and_publish(
        self,
        logical: LogicalSandbox,
        allocation: PhysicalAllocation,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> SandboxHandle:
        if allocation.provider_id is None:  # pragma: no cover - caller invariant
            raise RuntimeError("cannot wait for an allocation without provider ID")
        self._check_deadline(deadline_at)
        try:
            ready = await self._provider.wait_ready(
                ProviderAllocationRef(
                    provider_id=allocation.provider_id,
                    provider_instance_id=allocation.provider_instance_id,
                    allocation_id=allocation.allocation_id,
                    allocation_token=allocation.allocation_token,
                    key=allocation.key,
                ),
                profile=profile,
                deadline_at=deadline_at,
            )
        except ProviderNotReady as exc:
            retry_at = datetime.now(timezone.utc) + timedelta(
                milliseconds=exc.retry_after_ms
            )
            async with self._database.uow() as uow:
                allocation = await uow.repository.mark_allocation_provisioning_retry(
                    allocation.allocation_id,
                    retry_after=retry_at,
                    error_code=ErrorCode.PROVISIONING.value,
                )
                logical = await uow.repository.get_logical(logical.key)
                await uow.commit()
            if logical is None:  # pragma: no cover - state corruption
                raise RuntimeError("logical sandbox disappeared during readiness")
            return self._handle(logical, allocation)
        except ProviderAllocationFailed as exc:
            cleanup_pending = False
            try:
                # The provider acknowledged this exact resource. Delete that
                # identity before releasing its durable admission reservation;
                # otherwise a failed boot can leak both provider capacity and
                # an untracked sandbox. Provider I/O deliberately occurs with
                # no SQLAlchemy unit of work open.
                await self._provider.destroy_allocation(
                    ProviderAllocationRef(
                        provider_id=allocation.provider_id,
                        provider_instance_id=allocation.provider_instance_id,
                        allocation_id=allocation.allocation_id,
                        allocation_token=allocation.allocation_token,
                        key=allocation.key,
                    ),
                    deadline_at=deadline_at,
                )
            except ProviderLifecycleError:
                cleanup_pending = True
                retry_at = datetime.now(timezone.utc) + timedelta(seconds=1)
                async with self._database.uow() as uow:
                    await uow.repository.mark_allocation_provisioning_retry(
                        allocation.allocation_id,
                        retry_after=retry_at,
                        error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                    )
                    await uow.commit()
            else:
                async with self._database.uow() as uow:
                    await uow.repository.mark_create_failed(
                        allocation.allocation_token,
                        error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
                    )
                    await uow.commit()
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                (
                    "provider allocation failed; exact cleanup is pending "
                    "reconciliation"
                    if cleanup_pending
                    else "provider allocation failed and was removed"
                ),
                retry=(
                    RetryDisposition.WAIT
                    if cleanup_pending
                    else RetryDisposition.SAFE_SAME_OPERATION
                ),
                status_code=503,
                retry_after_ms=1000 if cleanup_pending else None,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        if ready.provider_id != allocation.provider_id:
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "provider readiness returned a different allocation identity",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=502,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            )

        if allocation.state == AllocationState.ACTIVE:
            return self._handle(logical, allocation)

        # Transaction 4: publish only the exact allocation proven ready.
        async with self._database.uow() as uow:
            allocation = await uow.repository.acknowledge_create(
                allocation.allocation_token,
                provider_id=ready.provider_id,
                provider_instance_id=ready.provider_instance_id,
            )
            allocation = await uow.repository.publish_allocation(
                allocation.allocation_token
            )
            logical = await uow.repository.get_logical(logical.key)
            await uow.commit()
        if logical is None:  # pragma: no cover - state corruption
            raise RuntimeError("logical sandbox disappeared after publication")
        return self._handle(logical, allocation)

    async def _retire_superseded_native_workspace(
        self,
        allocation: PhysicalAllocation,
        *,
        deadline_at: datetime,
    ) -> None:
        """Fence and discard sandbox-native storage before rebinding its workspace."""

        if allocation.provider_id is not None:
            try:
                if allocation.state != AllocationState.RELEASED:
                    await self._provider.release_allocation(
                        ProviderAllocationRef(
                            provider_id=allocation.provider_id,
                            provider_instance_id=allocation.provider_instance_id,
                            allocation_id=allocation.allocation_id,
                            allocation_token=allocation.allocation_token,
                            key=allocation.key,
                        ),
                        deadline_at=deadline_at,
                    )
                await self._provider.destroy_allocation(
                    ProviderAllocationRef(
                        provider_id=allocation.provider_id,
                        provider_instance_id=allocation.provider_instance_id,
                        allocation_id=allocation.allocation_id,
                        allocation_token=allocation.allocation_token,
                        key=allocation.key,
                    ),
                    deadline_at=deadline_at,
                )
            except ProviderLifecycleError as exc:
                raise AgentBoxError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "superseded sandbox-native workspace cleanup is pending",
                    retry=RetryDisposition.WAIT,
                    status_code=503,
                    retry_after_ms=1000,
                    context=ProviderErrorContext(
                        kind="provider", provider_name=self._provider.name
                    ),
                ) from exc
        async with self._database.uow() as uow:
            await uow.repository.mark_create_failed(
                allocation.allocation_token,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
            )
            await uow.commit()

    async def inspect(self, key: SandboxKey) -> SandboxHandle | None:
        """Read durable state only; provider inventory is never on this path."""

        async with self._database.uow() as uow:
            logical = await uow.repository.get_logical(key)
            allocation = await uow.repository.latest_allocation(key)
            await uow.commit()
        if logical is None:
            return None
        return self._handle(logical, allocation)

    async def release(
        self,
        key: SandboxKey,
        *,
        deadline_at: datetime,
        _claim: SandboxMaintenanceClaim | None = None,
    ) -> SandboxHandle:
        self._check_deadline(deadline_at)
        async with self._database.uow() as uow:
            claim, logical, allocation = await uow.repository.begin_release(
                key,
                claimed_until=deadline_at,
                retention_seconds=self._workspace_retention_seconds,
                claim=_claim,
            )
            await uow.commit()
        if allocation is None or allocation.provider_id is None:
            async with self._database.uow() as uow:
                logical, allocation = await uow.repository.complete_release(
                    key,
                    allocation.allocation_id if allocation is not None else None,
                    claim_token=claim.token,
                )
                await uow.commit()
            return self._handle(logical, allocation)
        provider_ref = ProviderAllocationRef(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
            allocation_id=allocation.allocation_id,
            allocation_token=allocation.allocation_token,
            key=allocation.key,
        )
        try:
            if key.workload_kind == WorkloadKind.WORKSPACE:
                await self._provider.release_allocation(
                    provider_ref, deadline_at=deadline_at
                )
            else:
                await self._provider.destroy_allocation(
                    provider_ref, deadline_at=deadline_at
                )
        except ProviderLifecycleError as exc:
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "sandbox release is pending provider reconciliation",
                retry=RetryDisposition.WAIT,
                status_code=503,
                retry_after_ms=1000,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        async with self._database.uow() as uow:
            logical, allocation = await uow.repository.complete_release(
                key,
                allocation.allocation_id,
                claim_token=claim.token,
            )
            await uow.commit()
        return self._handle(logical, allocation)

    async def destroy(
        self,
        key: SandboxKey,
        *,
        deadline_at: datetime,
        _claim: SandboxMaintenanceClaim | None = None,
    ) -> bool:
        self._check_deadline(deadline_at)
        recreate_on_ensure = (
            _claim is not None and key.workload_kind == WorkloadKind.WORKSPACE
        )
        async with self._database.uow() as uow:
            claim, _logical, allocation, storage = await uow.repository.begin_destroy(
                key,
                claimed_until=deadline_at,
                claim=_claim,
                recreate_on_ensure=recreate_on_ensure,
            )
            await uow.commit()
        try:
            if allocation is not None and allocation.provider_id is not None:
                await self._provider.destroy_allocation(
                    ProviderAllocationRef(
                        provider_id=allocation.provider_id,
                        provider_instance_id=allocation.provider_instance_id,
                        allocation_id=allocation.allocation_id,
                        allocation_token=allocation.allocation_token,
                        key=allocation.key,
                    ),
                    deadline_at=deadline_at,
                )
            allocation_owns_storage = (
                allocation is not None
                and storage is not None
                and storage.bound_allocation_id == allocation.allocation_id
                and storage.provider_storage_id == allocation.provider_id
            )
            if (
                storage is not None
                and storage.provider_storage_id is not None
                and not allocation_owns_storage
            ):
                await self._provider.destroy_workspace_storage(
                    storage.provider_storage_id, deadline_at=deadline_at
                )
        except ProviderLifecycleError as exc:
            raise AgentBoxError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "sandbox deletion is pending provider reconciliation",
                retry=RetryDisposition.WAIT,
                status_code=503,
                retry_after_ms=1000,
                context=ProviderErrorContext(
                    kind="provider", provider_name=self._provider.name
                ),
            ) from exc
        async with self._database.uow() as uow:
            await uow.repository.complete_destroy(
                key,
                allocation_id=(
                    allocation.allocation_id if allocation is not None else None
                ),
                claim_token=claim.token,
                recreate_on_ensure=recreate_on_ensure,
            )
            await uow.commit()
        return True

    def _admission_error(self, decision: ProviderAdmissionDecision) -> AgentBoxError:
        code = decision.error_code or ErrorCode.CAPACITY_EXHAUSTED
        message = (
            "provider create admission is temporarily rate limited"
            if code == ErrorCode.RATE_LIMITED
            else "provider active sandbox capacity is exhausted"
        )
        return AgentBoxError(
            code,
            message,
            retry=RetryDisposition.WAIT,
            status_code=429,
            retry_after_ms=decision.retry_after_ms,
            context=CapacityErrorContext(
                kind="capacity",
                provider_scope=self._provider.scope,
                active=decision.active,
                reserved=decision.reserved,
                limit=decision.limit,
            ),
        )

    @staticmethod
    def _handle(
        logical: LogicalSandbox, allocation: PhysicalAllocation | None
    ) -> SandboxHandle:
        return SandboxHandle(
            key=logical.key,
            desired_state=logical.desired_state,
            profile=logical.profile,
            allocation_state=allocation.state if allocation is not None else None,
            allocation_id=(
                allocation.allocation_id if allocation is not None else None
            ),
            allocation_epoch=logical.allocation_epoch,
            ready=(
                allocation is not None
                and allocation.state == AllocationState.ACTIVE
                and logical.current_allocation_id == allocation.allocation_id
            ),
            operation_id=(
                allocation.allocation_id
                if allocation is not None and allocation.state != AllocationState.ACTIVE
                else None
            ),
            retry_after_ms=SandboxLifecycleService._retry_after_ms(allocation),
        )

    @staticmethod
    def _retry_after_ms(allocation: PhysicalAllocation | None) -> int | None:
        if allocation is None or allocation.retry_after is None:
            return None
        remaining = allocation.retry_after - datetime.now(timezone.utc)
        return max(0, int(remaining.total_seconds() * 1000))

    async def _get_allocation(self, allocation_token: UUID) -> PhysicalAllocation:
        async with self._database.uow() as uow:
            allocation = await uow.repository.get_allocation_by_token(allocation_token)
            await uow.commit()
        if allocation is None:  # pragma: no cover - state corruption
            raise RuntimeError("allocation disappeared after dispatch claim")
        return allocation

    async def _mark_create_unknown_durably(
        self,
        allocation_token: UUID,
    ) -> None:
        async def persist() -> None:
            async with self._database.uow() as uow:
                await uow.repository.mark_create_unknown(
                    allocation_token,
                    reconcile_after=datetime.now(timezone.utc) + timedelta(seconds=1),
                    error_code=ErrorCode.AMBIGUOUS_CREATE.value,
                )
                await uow.commit()

        task = create_inherited_task(
            persist(),
            name=f"agentbox-create-cancel:{allocation_token}",
        )
        return await self._await_durable_write(task)

    async def _mark_create_failed_durably(
        self,
        allocation_token: UUID,
        *,
        error_code: ErrorCode,
    ) -> None:
        async def persist() -> None:
            async with self._database.uow() as uow:
                await uow.repository.mark_create_failed(
                    allocation_token,
                    error_code=error_code.value,
                )
                await uow.commit()

        task = create_inherited_task(
            persist(),
            name=f"agentbox-create-failed:{allocation_token}",
        )
        return await self._await_durable_write(task)

    @staticmethod
    async def _await_durable_write(task: asyncio.Task[None]) -> None:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Repeated caller cancellation must not interrupt the state
                # transition that makes the provider outcome reconcilable.
                if task.done():
                    return task.result()

    @staticmethod
    def _create_request_hash(
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        provider_name: str,
        provider_scope: str,
        admission_class: AdmissionClass,
    ) -> str:
        canonical = "\x1f".join(
            (
                key.workload_kind.value,
                str(key.logical_id),
                profile.name,
                profile.digest,
                provider_name,
                provider_scope,
                admission_class.value,
            )
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _check_deadline(deadline_at: datetime) -> None:
        if deadline_at.tzinfo is None:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "deadline_at must include a timezone",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        if deadline_at <= datetime.now(timezone.utc):
            raise AgentBoxError(
                ErrorCode.DEADLINE_EXCEEDED,
                "operation deadline has elapsed",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=408,
            )

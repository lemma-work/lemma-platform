from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentbox.domain import (
    AdmissionClass,
    AdmissionState,
    AgentBoxError,
    AllocationErrorContext,
    AllocationIntent,
    AllocationState,
    CreateReconcileCandidate,
    DispatchState,
    ErrorCode,
    LogicalSandbox,
    MaintenanceAction,
    OperationConflictContext,
    PhysicalAllocation,
    ProviderAdmissionDecision,
    ProviderAdmissionPolicy,
    ProcessIntent,
    ProcessState,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    PythonSessionState,
    RetryDisposition,
    SandboxDesiredState,
    SandboxKey,
    SandboxMaintenanceClaim,
    SandboxProfileRef,
    StorageKind,
    StorageState,
    WorkspaceStorage,
    WorkloadKind,
    utc_now,
)

from .models import (
    AllocationRow,
    CreateAttemptRow,
    LogicalSandboxRow,
    ProcessIntentRow,
    ProviderAdmissionRow,
    PythonExecutionRow,
    SessionRow,
    WorkspaceStorageRow,
)


NONTERMINAL_ALLOCATION_STATES = (
    AllocationState.RESERVED.value,
    AllocationState.PROVISIONING.value,
    AllocationState.UNKNOWN.value,
    AllocationState.ACTIVE.value,
    AllocationState.QUIESCING.value,
    AllocationState.DRAINING.value,
)

NONTERMINAL_PROCESS_STATES = (
    ProcessState.RESERVED.value,
    ProcessState.STARTING.value,
    ProcessState.UNKNOWN.value,
    ProcessState.RUNNING.value,
)


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


class AgentBoxRepository:
    """Persistence adapter; ORM entities never escape this class."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name

    async def ensure_logical(
        self,
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        now: datetime | None = None,
    ) -> LogicalSandbox:
        timestamp = now or utc_now()
        values = {
            "workload_kind": key.workload_kind.value,
            "logical_id": key.logical_id,
            "desired_state": SandboxDesiredState.PRESENT.value,
            "profile_name": profile.name,
            "profile_digest": profile.digest,
            "allocation_epoch": 0,
            "last_used_at": timestamp,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if self._dialect_name == "postgresql":
            statement = postgresql_insert(LogicalSandboxRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workload_kind", "logical_id"]
            )
        elif self._dialect_name == "sqlite":
            statement = sqlite_insert(LogicalSandboxRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workload_kind", "logical_id"]
            )
        else:  # pragma: no cover - startup rejects unsupported dialects
            raise RuntimeError(
                f"unsupported AgentBox state dialect: {self._dialect_name}"
            )
        await self._session.execute(statement)

        row = await self._select_logical(key, for_update=True)
        if row is None:  # pragma: no cover - constraint/transaction corruption
            raise RuntimeError("logical sandbox insert was not observable")
        if row.desired_state == SandboxDesiredState.DELETED.value:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "sandbox is permanently deleted",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.maintenance_action is not None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_QUIESCING,
                "sandbox lifecycle maintenance is in progress",
                retry=RetryDisposition.WAIT,
                status_code=409,
                retry_after_ms=1000,
            )
        if row.profile_digest != profile.digest:
            row.profile_name = profile.name
            row.profile_digest = profile.digest
        row.desired_state = SandboxDesiredState.PRESENT.value
        row.last_used_at = timestamp
        row.updated_at = timestamp
        return self._logical(row)

    async def get_logical(
        self, key: SandboxKey, *, for_update: bool = False
    ) -> LogicalSandbox | None:
        row = await self._select_logical(key, for_update=for_update)
        return self._logical(row) if row is not None else None

    async def protect_port_access(
        self,
        key: SandboxKey,
        *,
        until: datetime,
        now: datetime | None = None,
    ) -> LogicalSandbox:
        timestamp = now or utc_now()
        row = await self._select_logical(key, for_update=True)
        if row is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "sandbox does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        current = _aware(row.protected_until)
        if current is None or until > current:
            row.protected_until = until
        row.last_used_at = timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._logical(row)

    async def _select_logical(
        self, key: SandboxKey, *, for_update: bool
    ) -> LogicalSandboxRow | None:
        statement: Select[tuple[LogicalSandboxRow]] = select(LogicalSandboxRow).where(
            LogicalSandboxRow.workload_kind == key.workload_kind.value,
            LogicalSandboxRow.logical_id == key.logical_id,
        )
        if for_update and self._dialect_name == "postgresql":
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def begin_allocation(
        self,
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        provider_name: str,
        provider_scope: str,
        admission_class: str,
        request_hash: str,
        now: datetime | None = None,
    ) -> AllocationIntent:
        timestamp = now or utc_now()
        logical_row = await self._select_logical(key, for_update=True)
        if logical_row is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "logical sandbox does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )

        existing = await self._session.scalar(
            select(AllocationRow)
            .where(
                AllocationRow.workload_kind == key.workload_kind.value,
                AllocationRow.logical_id == key.logical_id,
                AllocationRow.profile_digest == profile.digest,
                AllocationRow.state.in_(NONTERMINAL_ALLOCATION_STATES),
            )
            .order_by(AllocationRow.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            attempt = await self._session.get(
                CreateAttemptRow, existing.allocation_token
            )
            if attempt is None:  # pragma: no cover - FK invariant
                raise RuntimeError("allocation is missing create attempt")
            return AllocationIntent(
                logical=self._logical(logical_row),
                allocation=self._allocation(existing),
                dispatch_state=DispatchState(attempt.dispatch_state),
                should_dispatch_create=False,
            )

        allocation_id = uuid4()
        allocation_token = uuid4()
        allocation = AllocationRow(
            allocation_id=allocation_id,
            workload_kind=key.workload_kind.value,
            logical_id=key.logical_id,
            allocation_token=allocation_token,
            provider_name=provider_name,
            provider_scope=provider_scope,
            profile_name=profile.name,
            profile_digest=profile.digest,
            state=AllocationState.RESERVED.value,
            admission_class=admission_class,
            admission_state=AdmissionState.UNRESERVED.value,
            created_at=timestamp,
            updated_at=timestamp,
        )
        attempt = CreateAttemptRow(
            allocation_token=allocation_token,
            request_hash=request_hash,
            dispatch_state=DispatchState.RESERVED.value,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._session.add_all((allocation, attempt))
        await self._session.flush()
        return AllocationIntent(
            logical=self._logical(logical_row),
            allocation=self._allocation(allocation),
            dispatch_state=DispatchState.RESERVED,
            should_dispatch_create=True,
        )

    async def reserve_provider_capacity(
        self,
        allocation_id: UUID,
        *,
        admission_class: AdmissionClass,
        policy: ProviderAdmissionPolicy,
        now: datetime | None = None,
    ) -> ProviderAdmissionDecision:
        """Atomically reserve active capacity and one create-rate token."""

        timestamp = now or utc_now()
        allocation = await self._session.get(AllocationRow, allocation_id)
        if allocation is None:
            raise RuntimeError("admission allocation does not exist")
        if allocation.admission_state in {
            AdmissionState.RESERVED.value,
            AdmissionState.ACTIVE.value,
        }:
            active, reserved = await self._admission_counts(allocation.provider_scope)
            return ProviderAdmissionDecision(
                accepted=True,
                active=active,
                reserved=reserved,
                limit=policy.max_active,
            )

        values = {
            "provider_scope": allocation.provider_scope,
            "max_active": policy.max_active,
            "active_count": 0,
            "reserved_count": 0,
            "create_tokens": float(policy.create_burst),
            "create_rate_per_second": policy.create_rate_per_second,
            "create_burst": policy.create_burst,
            "token_updated_at": timestamp,
            "interactive_capacity_reserve": policy.interactive_capacity_reserve,
            "latency_capacity_reserve": policy.latency_capacity_reserve,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if self._dialect_name == "postgresql":
            statement = postgresql_insert(ProviderAdmissionRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["provider_scope"]
            )
        elif self._dialect_name == "sqlite":
            statement = sqlite_insert(ProviderAdmissionRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["provider_scope"]
            )
        else:  # pragma: no cover
            raise RuntimeError(
                f"unsupported AgentBox state dialect: {self._dialect_name}"
            )
        await self._session.execute(statement)
        query = select(ProviderAdmissionRow).where(
            ProviderAdmissionRow.provider_scope == allocation.provider_scope
        )
        if self._dialect_name == "postgresql":
            query = query.with_for_update()
        admission = await self._session.scalar(query)
        if admission is None:  # pragma: no cover
            raise RuntimeError("provider admission row was not observable")

        admission.max_active = policy.max_active
        admission.create_rate_per_second = policy.create_rate_per_second
        admission.create_burst = policy.create_burst
        admission.interactive_capacity_reserve = policy.interactive_capacity_reserve
        admission.latency_capacity_reserve = policy.latency_capacity_reserve
        elapsed = max(
            0.0,
            (
                timestamp - (_aware(admission.token_updated_at) or timestamp)
            ).total_seconds(),
        )
        tokens = min(
            float(policy.create_burst),
            float(admission.create_tokens) + elapsed * policy.create_rate_per_second,
        )
        admission.create_tokens = tokens
        admission.token_updated_at = timestamp
        active, reserved = await self._admission_counts(allocation.provider_scope)
        admission.active_count = active
        admission.reserved_count = reserved
        admission.updated_at = timestamp

        if admission.blocked_until is not None:
            blocked_until = _aware(admission.blocked_until) or timestamp
            if blocked_until > timestamp:
                return ProviderAdmissionDecision(
                    accepted=False,
                    active=active,
                    reserved=reserved,
                    limit=policy.max_active,
                    error_code=ErrorCode.RATE_LIMITED,
                    retry_after_ms=max(
                        1, int((blocked_until - timestamp).total_seconds() * 1000)
                    ),
                )
            admission.blocked_until = None

        class_limit = policy.max_active
        if admission_class == AdmissionClass.LATENCY:
            class_limit -= policy.interactive_capacity_reserve
        elif admission_class == AdmissionClass.BATCH:
            class_limit -= (
                policy.interactive_capacity_reserve + policy.latency_capacity_reserve
            )
        if active + reserved >= class_limit:
            return ProviderAdmissionDecision(
                accepted=False,
                active=active,
                reserved=reserved,
                limit=class_limit,
                error_code=ErrorCode.CAPACITY_EXHAUSTED,
                retry_after_ms=1_000,
            )
        if tokens < 1:
            retry_after_ms = max(
                1,
                int(((1 - tokens) / policy.create_rate_per_second) * 1000),
            )
            return ProviderAdmissionDecision(
                accepted=False,
                active=active,
                reserved=reserved,
                limit=class_limit,
                error_code=ErrorCode.RATE_LIMITED,
                retry_after_ms=retry_after_ms,
            )

        admission.create_tokens = tokens - 1
        admission.reserved_count = reserved + 1
        await self._set_admission_state(
            allocation, AdmissionState.RESERVED, now=timestamp
        )
        allocation.updated_at = timestamp
        await self._session.flush()
        return ProviderAdmissionDecision(
            accepted=True,
            active=active,
            reserved=reserved + 1,
            limit=class_limit,
        )

    async def block_provider_creates(
        self,
        provider_scope: str,
        *,
        blocked_until: datetime,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        await self._session.execute(
            update(ProviderAdmissionRow)
            .where(ProviderAdmissionRow.provider_scope == provider_scope)
            .values(blocked_until=blocked_until, updated_at=timestamp)
        )

    async def _admission_counts(self, provider_scope: str) -> tuple[int, int]:
        active = int(
            await self._session.scalar(
                select(func.count(AllocationRow.allocation_id)).where(
                    AllocationRow.provider_scope == provider_scope,
                    AllocationRow.admission_state == AdmissionState.ACTIVE.value,
                )
            )
            or 0
        )
        reserved = int(
            await self._session.scalar(
                select(func.count(AllocationRow.allocation_id)).where(
                    AllocationRow.provider_scope == provider_scope,
                    AllocationRow.admission_state == AdmissionState.RESERVED.value,
                )
            )
            or 0
        )
        return active, reserved

    async def _set_admission_state(
        self,
        allocation: AllocationRow,
        state: AdmissionState,
        *,
        now: datetime,
    ) -> None:
        previous = AdmissionState(allocation.admission_state)
        if previous == state:
            return
        active, reserved = await self._admission_counts(allocation.provider_scope)
        allocation.admission_state = state.value
        admission = await self._session.get(
            ProviderAdmissionRow, allocation.provider_scope
        )
        if admission is not None:
            if previous == AdmissionState.ACTIVE:
                active -= 1
            elif previous == AdmissionState.RESERVED:
                reserved -= 1
            if state == AdmissionState.ACTIVE:
                active += 1
            elif state == AdmissionState.RESERVED:
                reserved += 1
            admission.active_count = active
            admission.reserved_count = reserved
            admission.updated_at = now

    async def ensure_workspace_storage(
        self,
        key: SandboxKey,
        *,
        provider_name: str,
        storage_kind: StorageKind,
        now: datetime | None = None,
    ) -> WorkspaceStorage:
        if key.workload_kind != WorkloadKind.WORKSPACE:
            raise AgentBoxError(
                ErrorCode.INVALID_REQUEST,
                "function workloads cannot have persistent workspace storage",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        timestamp = now or utc_now()
        values = {
            "workload_kind": key.workload_kind.value,
            "logical_id": key.logical_id,
            "provider_name": provider_name,
            "storage_kind": storage_kind.value,
            "state": StorageState.PROVISIONING.value,
            "content_generation": 0,
            "delete_token": uuid4(),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if self._dialect_name == "postgresql":
            statement = postgresql_insert(WorkspaceStorageRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workload_kind", "logical_id"]
            )
        elif self._dialect_name == "sqlite":
            statement = sqlite_insert(WorkspaceStorageRow).values(**values)
            statement = statement.on_conflict_do_nothing(
                index_elements=["workload_kind", "logical_id"]
            )
        else:  # pragma: no cover
            raise RuntimeError(
                f"unsupported AgentBox state dialect: {self._dialect_name}"
            )
        await self._session.execute(statement)
        row = await self._session.scalar(
            select(WorkspaceStorageRow).where(
                WorkspaceStorageRow.workload_kind == key.workload_kind.value,
                WorkspaceStorageRow.logical_id == key.logical_id,
            )
        )
        if row is None or row.delete_token is None:  # pragma: no cover
            raise RuntimeError("workspace storage insert was not observable")
        if row.provider_name != provider_name or row.storage_kind != storage_kind.value:
            raise AgentBoxError(
                ErrorCode.OPERATION_CONFLICT,
                "workspace storage belongs to another provider/storage kind",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        return self._storage(row)

    async def bind_workspace_storage(
        self,
        key: SandboxKey,
        *,
        provider_storage_id: str,
        allocation_id: UUID | None,
        now: datetime | None = None,
    ) -> WorkspaceStorage:
        timestamp = now or utc_now()
        row = await self._session.scalar(
            select(WorkspaceStorageRow)
            .where(
                WorkspaceStorageRow.workload_kind == key.workload_kind.value,
                WorkspaceStorageRow.logical_id == key.logical_id,
            )
            .with_for_update()
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "workspace storage does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.provider_storage_id not in (None, provider_storage_id):
            stale_sandbox_native_binding = False
            if (
                row.storage_kind == StorageKind.SANDBOX_NATIVE.value
                and row.bound_allocation_id is not None
                and row.bound_allocation_id != allocation_id
            ):
                bound_allocation = await self._session.get(
                    AllocationRow, row.bound_allocation_id
                )
                stale_sandbox_native_binding = (
                    bound_allocation is None
                    or bound_allocation.state
                    in (
                        AllocationState.ERROR.value,
                        AllocationState.DESTROYED.value,
                    )
                )
            if not stale_sandbox_native_binding:
                raise AgentBoxError(
                    ErrorCode.OPERATION_CONFLICT,
                    "workspace storage is already bound to another provider resource",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                )
        row.provider_storage_id = provider_storage_id
        row.bound_allocation_id = allocation_id
        row.state = StorageState.READY.value
        row.updated_at = timestamp
        await self._session.flush()
        return self._storage(row)

    async def get_workspace_storage(self, key: SandboxKey) -> WorkspaceStorage | None:
        row = await self._session.scalar(
            select(WorkspaceStorageRow).where(
                WorkspaceStorageRow.workload_kind == key.workload_kind.value,
                WorkspaceStorageRow.logical_id == key.logical_id,
            )
        )
        return self._storage(row) if row is not None else None

    async def mark_create_dispatched(
        self, allocation_token: UUID, *, now: datetime | None = None
    ) -> bool:
        timestamp = now or utc_now()
        result = await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state == DispatchState.RESERVED.value,
            )
            .values(
                dispatch_state=DispatchState.DISPATCHED.value,
                dispatch_started_at=timestamp,
                updated_at=timestamp,
            )
        )
        if result.rowcount != 1:
            return False
        await self._session.execute(
            update(AllocationRow)
            .where(
                AllocationRow.allocation_token == allocation_token,
                AllocationRow.state == AllocationState.RESERVED.value,
            )
            .values(state=AllocationState.PROVISIONING.value, updated_at=timestamp)
        )
        return True

    async def mark_create_unknown(
        self,
        allocation_token: UUID,
        *,
        reconcile_after: datetime,
        error_code: str,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state.in_(
                    (DispatchState.DISPATCHED.value, DispatchState.UNKNOWN.value)
                ),
            )
            .values(
                dispatch_state=DispatchState.UNKNOWN.value,
                reconcile_after=reconcile_after,
                updated_at=timestamp,
            )
        )
        await self._session.execute(
            update(AllocationRow)
            .where(AllocationRow.allocation_token == allocation_token)
            .values(
                state=AllocationState.UNKNOWN.value,
                last_error_code=error_code,
                updated_at=timestamp,
            )
        )

    async def list_due_create_reconciliation(
        self,
        provider_scope: str,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[CreateReconcileCandidate, ...]:
        timestamp = now or utc_now()
        rows = (
            await self._session.execute(
                select(AllocationRow, CreateAttemptRow)
                .join(
                    CreateAttemptRow,
                    CreateAttemptRow.allocation_token == AllocationRow.allocation_token,
                )
                .where(
                    AllocationRow.provider_scope == provider_scope,
                    CreateAttemptRow.dispatch_state.in_(
                        (
                            DispatchState.UNKNOWN.value,
                            DispatchState.ACKNOWLEDGED.value,
                        )
                    ),
                    CreateAttemptRow.reconcile_after.is_not(None),
                    CreateAttemptRow.reconcile_after <= timestamp,
                )
                .order_by(CreateAttemptRow.reconcile_after)
                .limit(limit)
            )
        ).all()
        return tuple(
            CreateReconcileCandidate(
                allocation=self._allocation(allocation),
                dispatch_state=DispatchState(attempt.dispatch_state),
                dispatch_started_at=(
                    _aware(attempt.dispatch_started_at)
                    or _aware(attempt.created_at)
                    or timestamp
                ),
                reconcile_after=_aware(attempt.reconcile_after) or timestamp,
            )
            for allocation, attempt in rows
        )

    async def claim_create_reconciliation(
        self,
        allocation_token: UUID,
        *,
        claimed_until: datetime,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        result = await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state.in_(
                    (
                        DispatchState.UNKNOWN.value,
                        DispatchState.ACKNOWLEDGED.value,
                    )
                ),
                CreateAttemptRow.reconcile_after.is_not(None),
                CreateAttemptRow.reconcile_after <= timestamp,
            )
            .values(
                last_reconcile_at=timestamp,
                reconcile_after=claimed_until,
                updated_at=timestamp,
            )
        )
        return result.rowcount == 1

    async def defer_create_reconciliation(
        self,
        allocation_token: UUID,
        *,
        reconcile_after: datetime,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state.in_(
                    (
                        DispatchState.UNKNOWN.value,
                        DispatchState.ACKNOWLEDGED.value,
                    )
                ),
            )
            .values(
                last_reconcile_at=timestamp,
                reconcile_after=reconcile_after,
                updated_at=timestamp,
            )
        )
        await self._session.execute(
            update(AllocationRow)
            .where(
                AllocationRow.allocation_token == allocation_token,
                AllocationRow.state == AllocationState.UNKNOWN.value,
            )
            .values(
                state=AllocationState.UNKNOWN.value,
                last_error_code=ErrorCode.AMBIGUOUS_CREATE.value,
                updated_at=timestamp,
            )
        )

    async def reset_create_after_rejection(
        self,
        allocation_token: UUID,
        *,
        retry_after: datetime,
        error_code: str,
        now: datetime | None = None,
    ) -> None:
        """Return a definitively unaccepted create to the same durable token."""

        timestamp = now or utc_now()
        await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state == DispatchState.DISPATCHED.value,
            )
            .values(
                dispatch_state=DispatchState.RESERVED.value,
                dispatch_started_at=None,
                reconcile_after=None,
                updated_at=timestamp,
            )
        )
        await self._session.execute(
            update(AllocationRow)
            .where(AllocationRow.allocation_token == allocation_token)
            .values(
                state=AllocationState.RESERVED.value,
                retry_after=retry_after,
                last_error_code=error_code,
                updated_at=timestamp,
            )
        )
        allocation = await self._session.scalar(
            select(AllocationRow).where(
                AllocationRow.allocation_token == allocation_token
            )
        )
        if allocation is not None:
            await self._set_admission_state(
                allocation, AdmissionState.UNRESERVED, now=timestamp
            )

    async def mark_create_failed(
        self,
        allocation_token: UUID,
        *,
        error_code: str,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        await self._session.execute(
            update(CreateAttemptRow)
            .where(
                CreateAttemptRow.allocation_token == allocation_token,
                CreateAttemptRow.dispatch_state.in_(
                    (
                        DispatchState.RESERVED.value,
                        DispatchState.DISPATCHED.value,
                        DispatchState.UNKNOWN.value,
                    )
                ),
            )
            .values(
                dispatch_state=DispatchState.RESOLVED.value,
                reconcile_after=None,
                updated_at=timestamp,
            )
        )
        allocation = await self._session.scalar(
            select(AllocationRow).where(
                AllocationRow.allocation_token == allocation_token
            )
        )
        if allocation is not None:
            allocation.state = AllocationState.ERROR.value
            allocation.last_error_code = error_code
            allocation.updated_at = timestamp
            await self._set_admission_state(
                allocation, AdmissionState.RELEASED, now=timestamp
            )

    async def mark_allocation_provisioning_retry(
        self,
        allocation_id: UUID,
        *,
        retry_after: datetime,
        error_code: str,
        now: datetime | None = None,
    ) -> PhysicalAllocation:
        timestamp = now or utc_now()
        row = await self._session.get(AllocationRow, allocation_id)
        if row is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "allocation does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        row.state = AllocationState.PROVISIONING.value
        row.retry_after = retry_after
        row.last_error_code = error_code
        row.updated_at = timestamp
        await self._session.execute(
            update(CreateAttemptRow)
            .where(CreateAttemptRow.allocation_token == row.allocation_token)
            .values(reconcile_after=retry_after, updated_at=timestamp)
        )
        await self._session.flush()
        return self._allocation(row)

    async def acknowledge_create(
        self,
        allocation_token: UUID,
        *,
        provider_id: str,
        provider_instance_id: str | None = None,
        provider_request_id: str | None = None,
        now: datetime | None = None,
    ) -> PhysicalAllocation:
        timestamp = now or utc_now()
        allocation = await self._session.scalar(
            select(AllocationRow)
            .where(AllocationRow.allocation_token == allocation_token)
            .with_for_update()
        )
        if allocation is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "allocation token is unknown",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if allocation.provider_id not in (None, provider_id):
            raise AgentBoxError(
                ErrorCode.OPERATION_CONFLICT,
                "allocation is already bound to another provider object",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
                context=AllocationErrorContext(
                    kind="allocation",
                    allocation_id=allocation.allocation_id,
                    allocation_epoch=allocation.allocation_epoch,
                ),
            )
        allocation.provider_id = provider_id
        allocation.provider_instance_id = provider_instance_id
        allocation.state = AllocationState.PROVISIONING.value
        allocation.updated_at = timestamp
        attempt = await self._session.get(CreateAttemptRow, allocation_token)
        if attempt is None:  # pragma: no cover - FK invariant
            raise RuntimeError("allocation is missing create attempt")
        attempt.dispatch_state = DispatchState.ACKNOWLEDGED.value
        attempt.provider_request_id = provider_request_id
        attempt.updated_at = timestamp
        await self._session.flush()
        return self._allocation(allocation)

    async def publish_allocation(
        self, allocation_token: UUID, *, now: datetime | None = None
    ) -> PhysicalAllocation:
        timestamp = now or utc_now()
        allocation = await self._session.scalar(
            select(AllocationRow)
            .where(AllocationRow.allocation_token == allocation_token)
            .with_for_update()
        )
        if allocation is None or allocation.provider_id is None:
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "allocation has not been acknowledged by the provider",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        key = SandboxKey(
            workload_kind=WorkloadKind(allocation.workload_kind),
            logical_id=allocation.logical_id,
        )
        logical = await self._select_logical(key, for_update=True)
        if logical is None:  # pragma: no cover - state invariant
            raise RuntimeError("allocation owner does not exist")

        previous_id = logical.current_allocation_id
        if previous_id == allocation.allocation_id and allocation.state == "active":
            return self._allocation(allocation)
        if previous_id is not None and previous_id != allocation.allocation_id:
            await self._session.execute(
                update(AllocationRow)
                .where(
                    AllocationRow.allocation_id == previous_id,
                    AllocationRow.state == AllocationState.ACTIVE.value,
                )
                .values(state=AllocationState.DRAINING.value, updated_at=timestamp)
            )

        logical.allocation_epoch += 1
        logical.current_allocation_id = allocation.allocation_id
        logical.desired_state = SandboxDesiredState.PRESENT.value
        logical.released_at = None
        logical.delete_after = None
        logical.last_used_at = timestamp
        logical.updated_at = timestamp
        allocation.state = AllocationState.ACTIVE.value
        await self._set_admission_state(
            allocation, AdmissionState.ACTIVE, now=timestamp
        )
        allocation.allocation_epoch = logical.allocation_epoch
        allocation.ready_at = timestamp
        allocation.last_error_code = None
        allocation.updated_at = timestamp
        attempt = await self._session.get(CreateAttemptRow, allocation_token)
        if attempt is not None:
            attempt.dispatch_state = DispatchState.RESOLVED.value
            attempt.reconcile_after = None
            attempt.updated_at = timestamp
        await self._session.flush()
        return self._allocation(allocation)

    async def current_allocation(self, key: SandboxKey) -> PhysicalAllocation | None:
        logical = await self._select_logical(key, for_update=False)
        if logical is None or logical.current_allocation_id is None:
            return None
        allocation = await self._session.get(
            AllocationRow, logical.current_allocation_id
        )
        return self._allocation(allocation) if allocation is not None else None

    async def resume_released_allocation(
        self,
        key: SandboxKey,
        profile: SandboxProfileRef,
        *,
        now: datetime | None = None,
    ) -> PhysicalAllocation | None:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None or logical.current_allocation_id is None:
            return None
        allocation = await self._session.get(
            AllocationRow, logical.current_allocation_id
        )
        if (
            allocation is None
            or allocation.state != AllocationState.RELEASED.value
            or allocation.profile_digest != profile.digest
            or key.workload_kind != WorkloadKind.WORKSPACE
        ):
            return None
        allocation.state = AllocationState.PROVISIONING.value
        allocation.retry_after = None
        allocation.updated_at = timestamp
        logical.desired_state = SandboxDesiredState.PRESENT.value
        logical.last_used_at = timestamp
        logical.updated_at = timestamp
        await self._session.flush()
        return self._allocation(allocation)

    async def claim_due_maintenance(
        self,
        *,
        workspace_idle_before: datetime,
        function_idle_before: datetime,
        claimed_until: datetime,
        now: datetime | None = None,
        limit: int = 1,
    ) -> tuple[SandboxMaintenanceClaim, ...]:
        """Claim due lifecycle work without keeping a DB session over provider I/O."""

        timestamp = now or utc_now()
        active_process = exists(
            select(ProcessIntentRow.operation_id).where(
                ProcessIntentRow.workload_kind == LogicalSandboxRow.workload_kind,
                ProcessIntentRow.logical_id == LogicalSandboxRow.logical_id,
                ProcessIntentRow.state.in_(NONTERMINAL_PROCESS_STATES),
            )
        )
        expired_claim = and_(
            LogicalSandboxRow.maintenance_action.is_not(None),
            or_(
                LogicalSandboxRow.maintenance_claimed_until.is_(None),
                LogicalSandboxRow.maintenance_claimed_until <= timestamp,
            ),
        )
        idle_workspace = and_(
            LogicalSandboxRow.workload_kind == WorkloadKind.WORKSPACE.value,
            LogicalSandboxRow.desired_state == SandboxDesiredState.PRESENT.value,
            LogicalSandboxRow.current_allocation_id.is_not(None),
            LogicalSandboxRow.last_used_at <= workspace_idle_before,
            or_(
                LogicalSandboxRow.protected_until.is_(None),
                LogicalSandboxRow.protected_until <= timestamp,
            ),
            ~active_process,
        )
        idle_function = and_(
            LogicalSandboxRow.workload_kind == WorkloadKind.FUNCTION.value,
            LogicalSandboxRow.desired_state == SandboxDesiredState.PRESENT.value,
            LogicalSandboxRow.current_allocation_id.is_not(None),
            LogicalSandboxRow.last_used_at <= function_idle_before,
            or_(
                LogicalSandboxRow.protected_until.is_(None),
                LogicalSandboxRow.protected_until <= timestamp,
            ),
            ~active_process,
        )
        expired_workspace = and_(
            LogicalSandboxRow.workload_kind == WorkloadKind.WORKSPACE.value,
            LogicalSandboxRow.desired_state == SandboxDesiredState.RELEASED.value,
            LogicalSandboxRow.delete_after.is_not(None),
            LogicalSandboxRow.delete_after <= timestamp,
        )
        statement = (
            select(LogicalSandboxRow)
            .where(or_(expired_claim, idle_workspace, idle_function, expired_workspace))
            .order_by(
                LogicalSandboxRow.maintenance_claimed_until.asc().nullsfirst(),
                LogicalSandboxRow.last_used_at.asc(),
            )
            .limit(limit)
        )
        if self._dialect_name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = (await self._session.scalars(statement)).all()
        claims: list[SandboxMaintenanceClaim] = []
        for row in rows:
            if row.maintenance_action is not None:
                action = MaintenanceAction(row.maintenance_action)
            elif row.desired_state == SandboxDesiredState.PRESENT.value:
                action = MaintenanceAction.RELEASE
            else:
                action = MaintenanceAction.DESTROY
            token = uuid4()
            row.maintenance_action = action.value
            row.maintenance_token = token
            row.maintenance_claimed_until = claimed_until
            row.updated_at = timestamp
            claims.append(
                SandboxMaintenanceClaim(
                    key=SandboxKey(
                        workload_kind=WorkloadKind(row.workload_kind),
                        logical_id=row.logical_id,
                    ),
                    action=action,
                    token=token,
                    claimed_until=claimed_until,
                )
            )
        await self._session.flush()
        return tuple(claims)

    def _take_maintenance_claim(
        self,
        logical: LogicalSandboxRow,
        *,
        action: MaintenanceAction,
        claimed_until: datetime,
        claim: SandboxMaintenanceClaim | None,
        now: datetime,
    ) -> SandboxMaintenanceClaim:
        if claim is not None:
            if (
                claim.key.workload_kind.value != logical.workload_kind
                or claim.key.logical_id != logical.logical_id
                or claim.action != action
                or logical.maintenance_token != claim.token
                or logical.maintenance_action != action.value
            ):
                raise AgentBoxError(
                    ErrorCode.ALLOCATION_CHANGED,
                    "sandbox maintenance claim is stale",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                )
            return claim
        if (
            logical.maintenance_action is not None
            and logical.maintenance_claimed_until is not None
            and _aware(logical.maintenance_claimed_until) > now
        ):
            raise AgentBoxError(
                ErrorCode.SANDBOX_QUIESCING,
                "sandbox lifecycle maintenance is already in progress",
                retry=RetryDisposition.WAIT,
                status_code=409,
                retry_after_ms=max(
                    1,
                    int(
                        (
                            _aware(logical.maintenance_claimed_until) - now
                        ).total_seconds()
                        * 1000
                    ),
                ),
            )
        token = uuid4()
        logical.maintenance_action = action.value
        logical.maintenance_token = token
        logical.maintenance_claimed_until = claimed_until
        return SandboxMaintenanceClaim(
            key=SandboxKey(
                workload_kind=WorkloadKind(logical.workload_kind),
                logical_id=logical.logical_id,
            ),
            action=action,
            token=token,
            claimed_until=claimed_until,
        )

    async def begin_release(
        self,
        key: SandboxKey,
        *,
        claimed_until: datetime,
        retention_seconds: float,
        claim: SandboxMaintenanceClaim | None = None,
        now: datetime | None = None,
    ) -> tuple[SandboxMaintenanceClaim, LogicalSandbox, PhysicalAllocation | None]:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "sandbox does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        maintenance_claim = self._take_maintenance_claim(
            logical,
            action=MaintenanceAction.RELEASE,
            claimed_until=claimed_until,
            claim=claim,
            now=timestamp,
        )
        logical.desired_state = SandboxDesiredState.RELEASED.value
        logical.released_at = timestamp
        logical.delete_after = (
            timestamp + timedelta(seconds=retention_seconds)
            if key.workload_kind == WorkloadKind.WORKSPACE
            else None
        )
        logical.updated_at = timestamp
        allocation: AllocationRow | None = None
        if logical.current_allocation_id is not None:
            allocation = await self._session.get(
                AllocationRow, logical.current_allocation_id
            )
            if allocation is not None and allocation.state not in {
                AllocationState.RELEASED.value,
                AllocationState.DESTROYED.value,
            }:
                allocation.state = AllocationState.QUIESCING.value
                allocation.updated_at = timestamp
        await self._session.execute(
            update(SessionRow)
            .where(
                SessionRow.workload_kind == key.workload_kind.value,
                SessionRow.logical_id == key.logical_id,
                SessionRow.state.not_in(
                    [PythonSessionState.DELETED.value, PythonSessionState.STALE.value]
                ),
            )
            .values(state=PythonSessionState.STALE.value, updated_at=timestamp)
        )
        await self._session.flush()
        return (
            maintenance_claim,
            self._logical(logical),
            self._allocation(allocation) if allocation is not None else None,
        )

    async def complete_release(
        self,
        key: SandboxKey,
        allocation_id: UUID | None,
        *,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> tuple[LogicalSandbox, PhysicalAllocation | None]:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        allocation = (
            await self._session.get(AllocationRow, allocation_id)
            if allocation_id is not None
            else None
        )
        if logical is None:
            raise RuntimeError("release owner disappeared")
        if (
            logical.maintenance_token != claim_token
            or logical.maintenance_action != MaintenanceAction.RELEASE.value
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "sandbox release claim is stale",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        if allocation_id is not None and allocation is None:
            raise RuntimeError("release allocation disappeared")
        if allocation_id is not None and logical.current_allocation_id != allocation_id:
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "release allocation is no longer current",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        if allocation is not None:
            allocation.state = (
                AllocationState.RELEASED.value
                if key.workload_kind == WorkloadKind.WORKSPACE
                else AllocationState.DESTROYED.value
            )
            allocation.released_at = timestamp
            if key.workload_kind == WorkloadKind.FUNCTION:
                allocation.destroyed_at = timestamp
                logical.current_allocation_id = None
            allocation.updated_at = timestamp
            await self._set_admission_state(
                allocation, AdmissionState.RELEASED, now=timestamp
            )
            await self._session.execute(
                update(ProcessIntentRow)
                .where(
                    ProcessIntentRow.allocation_id == allocation_id,
                    ProcessIntentRow.state.in_(NONTERMINAL_PROCESS_STATES),
                )
                .values(
                    state=ProcessState.CANCELLED.value,
                    completed_at=timestamp,
                    updated_at=timestamp,
                )
            )
        logical.maintenance_action = None
        logical.maintenance_token = None
        logical.maintenance_claimed_until = None
        logical.updated_at = timestamp
        await self._session.flush()
        return self._logical(logical), (
            self._allocation(allocation) if allocation is not None else None
        )

    async def begin_destroy(
        self,
        key: SandboxKey,
        *,
        claimed_until: datetime,
        claim: SandboxMaintenanceClaim | None = None,
        now: datetime | None = None,
    ) -> tuple[
        SandboxMaintenanceClaim,
        LogicalSandbox,
        PhysicalAllocation | None,
        WorkspaceStorage | None,
    ]:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "sandbox does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        maintenance_claim = self._take_maintenance_claim(
            logical,
            action=MaintenanceAction.DESTROY,
            claimed_until=claimed_until,
            claim=claim,
            now=timestamp,
        )
        logical.desired_state = SandboxDesiredState.DELETED.value
        logical.delete_after = None
        logical.updated_at = timestamp
        allocation: AllocationRow | None = None
        if logical.current_allocation_id is not None:
            allocation = await self._session.get(
                AllocationRow, logical.current_allocation_id
            )
            if (
                allocation is not None
                and allocation.state != AllocationState.DESTROYED.value
            ):
                allocation.state = AllocationState.DESTROYING.value
                allocation.updated_at = timestamp
        storage_row = await self._session.scalar(
            select(WorkspaceStorageRow).where(
                WorkspaceStorageRow.workload_kind == key.workload_kind.value,
                WorkspaceStorageRow.logical_id == key.logical_id,
            )
        )
        if storage_row is not None and storage_row.state != StorageState.DELETED.value:
            storage_row.state = StorageState.DELETING.value
            storage_row.updated_at = timestamp
        await self._session.execute(
            update(SessionRow)
            .where(
                SessionRow.workload_kind == key.workload_kind.value,
                SessionRow.logical_id == key.logical_id,
            )
            .values(state=PythonSessionState.DELETED.value, updated_at=timestamp)
        )
        await self._session.flush()
        return (
            maintenance_claim,
            self._logical(logical),
            self._allocation(allocation) if allocation is not None else None,
            self._storage(storage_row) if storage_row is not None else None,
        )

    async def complete_destroy(
        self,
        key: SandboxKey,
        *,
        allocation_id: UUID | None,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> None:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None:
            return
        if (
            logical.maintenance_token != claim_token
            or logical.maintenance_action != MaintenanceAction.DESTROY.value
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "sandbox deletion claim is stale",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        if allocation_id is not None:
            allocation = await self._session.get(AllocationRow, allocation_id)
            if allocation is not None:
                allocation.state = AllocationState.DESTROYED.value
                allocation.destroyed_at = timestamp
                allocation.updated_at = timestamp
                await self._set_admission_state(
                    allocation, AdmissionState.RELEASED, now=timestamp
                )
        storage_row = await self._session.scalar(
            select(WorkspaceStorageRow).where(
                WorkspaceStorageRow.workload_kind == key.workload_kind.value,
                WorkspaceStorageRow.logical_id == key.logical_id,
            )
        )
        if storage_row is not None:
            storage_row.state = StorageState.DELETED.value
            storage_row.deleted_at = timestamp
            storage_row.provider_storage_id = None
            storage_row.updated_at = timestamp
        logical.current_allocation_id = None
        logical.maintenance_action = None
        logical.maintenance_token = None
        logical.maintenance_claimed_until = None
        logical.updated_at = timestamp
        await self._session.flush()

    async def latest_allocation(self, key: SandboxKey) -> PhysicalAllocation | None:
        allocation = await self._session.scalar(
            select(AllocationRow)
            .where(
                AllocationRow.workload_kind == key.workload_kind.value,
                AllocationRow.logical_id == key.logical_id,
            )
            .order_by(AllocationRow.created_at.desc())
            .limit(1)
        )
        return self._allocation(allocation) if allocation is not None else None

    async def get_allocation_by_token(
        self, allocation_token: UUID
    ) -> PhysicalAllocation | None:
        allocation = await self._session.scalar(
            select(AllocationRow).where(
                AllocationRow.allocation_token == allocation_token
            )
        )
        return self._allocation(allocation) if allocation is not None else None

    async def list_allocations(self, key: SandboxKey) -> tuple[PhysicalAllocation, ...]:
        allocations = (
            await self._session.scalars(
                select(AllocationRow)
                .where(
                    AllocationRow.workload_kind == key.workload_kind.value,
                    AllocationRow.logical_id == key.logical_id,
                )
                .order_by(AllocationRow.created_at)
            )
        ).all()
        return tuple(self._allocation(row) for row in allocations)

    async def reserve_process(
        self,
        key: SandboxKey,
        *,
        operation_id: UUID,
        request_hash: str,
        env_keys: tuple[str, ...],
        cwd: str,
        tty: bool,
        output_limit_bytes: int,
        deadline_at: datetime,
        now: datetime | None = None,
    ) -> tuple[ProcessIntent, bool]:
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None or logical.current_allocation_id is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "sandbox has no current allocation",
                retry=RetryDisposition.WAIT,
                status_code=404,
            )
        if logical.desired_state != SandboxDesiredState.PRESENT.value:
            raise AgentBoxError(
                ErrorCode.SANDBOX_QUIESCING,
                "sandbox is not accepting new processes",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        allocation = await self._session.get(
            AllocationRow, logical.current_allocation_id
        )
        if allocation is None or allocation.state != AllocationState.ACTIVE.value:
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "sandbox allocation is not ready for processes",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        existing = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AgentBoxError(
                    ErrorCode.OPERATION_CONFLICT,
                    "operation ID was reused with a different request",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                    context=OperationConflictContext(
                        kind="operation_conflict", operation_id=operation_id
                    ),
                )
            return self._process(existing), False

        row = ProcessIntentRow(
            workload_kind=key.workload_kind.value,
            logical_id=key.logical_id,
            operation_id=operation_id,
            allocation_id=logical.current_allocation_id,
            allocation_epoch=logical.allocation_epoch,
            request_hash=request_hash,
            env_keys=list(env_keys),
            cwd=cwd,
            tty=tty,
            output_limit_bytes=output_limit_bytes,
            state=ProcessState.RESERVED.value,
            deadline_at=deadline_at,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._session.add(row)
        logical.last_used_at = timestamp
        logical.updated_at = timestamp
        await self._session.flush()
        return self._process(row), True

    async def mark_process_starting(
        self, key: SandboxKey, operation_id: UUID, *, now: datetime | None = None
    ) -> bool:
        timestamp = now or utc_now()
        result = await self._session.execute(
            update(ProcessIntentRow)
            .where(
                ProcessIntentRow.workload_kind == key.workload_kind.value,
                ProcessIntentRow.logical_id == key.logical_id,
                ProcessIntentRow.operation_id == operation_id,
                ProcessIntentRow.state == ProcessState.RESERVED.value,
            )
            .values(state=ProcessState.STARTING.value, updated_at=timestamp)
        )
        return result.rowcount == 1

    async def acknowledge_process(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        provider_process_id: str,
        provider_tag: str,
        now: datetime | None = None,
    ) -> ProcessIntent:
        timestamp = now or utc_now()
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.provider_process_id not in (None, provider_process_id):
            raise AgentBoxError(
                ErrorCode.OPERATION_CONFLICT,
                "process is already bound to another provider process",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
                context=OperationConflictContext(
                    kind="operation_conflict", operation_id=operation_id
                ),
            )
        row.provider_process_id = provider_process_id
        row.provider_tag = provider_tag
        row.state = ProcessState.RUNNING.value
        row.started_at = row.started_at or timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._process(row)

    async def mark_process_unknown(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ProcessIntent:
        timestamp = now or utc_now()
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state in {ProcessState.RESERVED.value, ProcessState.STARTING.value}:
            row.state = ProcessState.UNKNOWN.value
            row.updated_at = timestamp
            await self._session.flush()
        return self._process(row)

    async def reset_process_after_rejection(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ProcessIntent:
        timestamp = now or utc_now()
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state == ProcessState.STARTING.value:
            row.state = ProcessState.RESERVED.value
            row.updated_at = timestamp
            await self._session.flush()
        return self._process(row)

    async def mark_process_terminated(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        now: datetime | None = None,
    ) -> ProcessIntent:
        timestamp = now or utc_now()
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state not in {
            ProcessState.SUCCEEDED.value,
            ProcessState.FAILED.value,
            ProcessState.CANCELLED.value,
            ProcessState.TIMED_OUT.value,
        }:
            row.state = ProcessState.CANCELLED.value
            row.completed_at = timestamp
            row.updated_at = timestamp
            await self._session.flush()
        return self._process(row)

    async def complete_process(
        self,
        key: SandboxKey,
        operation_id: UUID,
        *,
        state: ProcessState,
        exit_code: int | None,
        now: datetime | None = None,
    ) -> ProcessIntent:
        if state not in {
            ProcessState.SUCCEEDED,
            ProcessState.FAILED,
            ProcessState.CANCELLED,
            ProcessState.TIMED_OUT,
        }:
            raise ValueError("completed process must have a terminal state")
        timestamp = now or utc_now()
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "process intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state in {
            ProcessState.SUCCEEDED.value,
            ProcessState.FAILED.value,
            ProcessState.CANCELLED.value,
            ProcessState.TIMED_OUT.value,
        }:
            return self._process(row)
        row.state = state.value
        row.exit_code = exit_code
        row.completed_at = timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._process(row)

    async def get_process(
        self, key: SandboxKey, operation_id: UUID
    ) -> ProcessIntent | None:
        row = await self._session.get(
            ProcessIntentRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        return self._process(row) if row is not None else None

    async def list_processes(self, key: SandboxKey) -> tuple[ProcessIntent, ...]:
        rows = (
            await self._session.scalars(
                select(ProcessIntentRow)
                .where(
                    ProcessIntentRow.workload_kind == key.workload_kind.value,
                    ProcessIntentRow.logical_id == key.logical_id,
                )
                .order_by(ProcessIntentRow.created_at)
            )
        ).all()
        return tuple(self._process(row) for row in rows)

    async def reserve_python_session(
        self,
        key: SandboxKey,
        *,
        session_id: UUID,
        cwd: str,
        env_keys: tuple[str, ...],
        now: datetime | None = None,
    ) -> tuple[PythonSessionRef, bool]:
        if key.workload_kind != WorkloadKind.WORKSPACE:
            raise AgentBoxError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "Python sessions are available only for workspaces",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=422,
            )
        timestamp = now or utc_now()
        logical = await self._select_logical(key, for_update=True)
        if logical is None or logical.current_allocation_id is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "workspace has no current allocation",
                retry=RetryDisposition.WAIT,
                status_code=404,
            )
        allocation = await self._session.get(
            AllocationRow, logical.current_allocation_id
        )
        if allocation is None or allocation.state != AllocationState.ACTIVE.value:
            raise AgentBoxError(
                ErrorCode.PROVISIONING,
                "workspace allocation is not ready for Python sessions",
                retry=RetryDisposition.WAIT,
                status_code=409,
            )
        row = await self._session.get(
            SessionRow, (key.workload_kind.value, key.logical_id, session_id)
        )
        if row is None:
            row = SessionRow(
                workload_kind=key.workload_kind.value,
                logical_id=key.logical_id,
                session_id=session_id,
                allocation_id=allocation.allocation_id,
                allocation_epoch=logical.allocation_epoch,
                provider_context_id=None,
                cwd=cwd,
                env_keys=list(env_keys),
                state=PythonSessionState.RESERVED.value,
                last_used_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._session.add(row)
            created = True
        elif (
            row.allocation_id != allocation.allocation_id
            or row.allocation_epoch != logical.allocation_epoch
            or row.state
            in {PythonSessionState.STALE.value, PythonSessionState.DELETED.value}
        ):
            row.allocation_id = allocation.allocation_id
            row.allocation_epoch = logical.allocation_epoch
            row.provider_context_id = None
            row.cwd = cwd
            row.env_keys = list(env_keys)
            row.state = PythonSessionState.RESERVED.value
            row.last_used_at = timestamp
            row.updated_at = timestamp
            created = True
        else:
            if row.cwd != cwd or tuple(row.env_keys) != env_keys:
                raise AgentBoxError(
                    ErrorCode.OPERATION_CONFLICT,
                    "session ID was reused with different configuration",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                )
            created = False
        logical.last_used_at = timestamp
        logical.updated_at = timestamp
        await self._session.flush()
        return self._python_session(row), created

    async def mark_python_session_creating(
        self, key: SandboxKey, session_id: UUID, *, now: datetime | None = None
    ) -> bool:
        timestamp = now or utc_now()
        result = await self._session.execute(
            update(SessionRow)
            .where(
                SessionRow.workload_kind == key.workload_kind.value,
                SessionRow.logical_id == key.logical_id,
                SessionRow.session_id == session_id,
                SessionRow.state == PythonSessionState.RESERVED.value,
            )
            .values(state=PythonSessionState.CREATING.value, updated_at=timestamp)
        )
        return result.rowcount == 1

    async def acknowledge_python_session(
        self,
        key: SandboxKey,
        session_id: UUID,
        *,
        provider_context_id: str,
        now: datetime | None = None,
    ) -> PythonSessionRef:
        timestamp = now or utc_now()
        row = await self._require_session_row(key, session_id)
        if row.provider_context_id not in (None, provider_context_id):
            raise AgentBoxError(
                ErrorCode.OPERATION_CONFLICT,
                "session is already bound to another provider context",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
            )
        row.provider_context_id = provider_context_id
        row.state = PythonSessionState.ACTIVE.value
        row.last_used_at = timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._python_session(row)

    async def mark_python_session_unknown(
        self, key: SandboxKey, session_id: UUID, *, now: datetime | None = None
    ) -> PythonSessionRef:
        timestamp = now or utc_now()
        row = await self._require_session_row(key, session_id)
        if row.state == PythonSessionState.CREATING.value:
            row.state = PythonSessionState.UNKNOWN.value
            row.updated_at = timestamp
            await self._session.flush()
        return self._python_session(row)

    async def get_python_session(
        self, key: SandboxKey, session_id: UUID
    ) -> PythonSessionRef | None:
        row = await self._session.get(
            SessionRow, (key.workload_kind.value, key.logical_id, session_id)
        )
        return self._python_session(row) if row is not None else None

    async def set_python_session_state(
        self,
        key: SandboxKey,
        session_id: UUID,
        state: PythonSessionState,
        *,
        provider_context_id: str | None = None,
        now: datetime | None = None,
    ) -> PythonSessionRef:
        timestamp = now or utc_now()
        row = await self._require_session_row(key, session_id)
        row.state = state.value
        row.provider_context_id = provider_context_id
        row.last_used_at = timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._python_session(row)

    async def reserve_python_execution(
        self,
        key: SandboxKey,
        session_id: UUID,
        *,
        operation_id: UUID,
        request_hash: str,
        deadline_at: datetime,
        now: datetime | None = None,
    ) -> tuple[PythonResult, bool]:
        timestamp = now or utc_now()
        session = await self._require_session_row(key, session_id)
        logical = await self._select_logical(key, for_update=True)
        if (
            session.state != PythonSessionState.ACTIVE.value
            or logical is None
            or logical.current_allocation_id != session.allocation_id
            or logical.allocation_epoch != session.allocation_epoch
        ):
            raise AgentBoxError(
                ErrorCode.ALLOCATION_CHANGED,
                "Python session does not belong to the current allocation",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=409,
                context=AllocationErrorContext(
                    kind="allocation",
                    allocation_id=session.allocation_id,
                    allocation_epoch=session.allocation_epoch,
                ),
            )
        existing = await self._session.get(
            PythonExecutionRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if existing is not None:
            if (
                existing.session_id != session_id
                or existing.request_hash != request_hash
            ):
                raise AgentBoxError(
                    ErrorCode.OPERATION_CONFLICT,
                    "Python operation ID was reused with a different request",
                    retry=RetryDisposition.DO_NOT_RETRY,
                    status_code=409,
                    context=OperationConflictContext(
                        kind="operation_conflict", operation_id=operation_id
                    ),
                )
            return self._python_result(existing), False
        row = PythonExecutionRow(
            workload_kind=key.workload_kind.value,
            logical_id=key.logical_id,
            operation_id=operation_id,
            session_id=session_id,
            allocation_id=session.allocation_id,
            allocation_epoch=session.allocation_epoch,
            request_hash=request_hash,
            state=PythonExecutionState.RESERVED.value,
            deadline_at=deadline_at,
            stdout="",
            stderr="",
            output_truncated=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._session.add(row)
        session.last_used_at = timestamp
        session.updated_at = timestamp
        logical.last_used_at = timestamp
        logical.updated_at = timestamp
        await self._session.flush()
        return self._python_result(row), True

    async def mark_python_execution_starting(
        self, key: SandboxKey, operation_id: UUID, *, now: datetime | None = None
    ) -> bool:
        timestamp = now or utc_now()
        result = await self._session.execute(
            update(PythonExecutionRow)
            .where(
                PythonExecutionRow.workload_kind == key.workload_kind.value,
                PythonExecutionRow.logical_id == key.logical_id,
                PythonExecutionRow.operation_id == operation_id,
                PythonExecutionRow.state == PythonExecutionState.RESERVED.value,
            )
            .values(state=PythonExecutionState.STARTING.value, updated_at=timestamp)
        )
        return result.rowcount == 1

    async def complete_python_execution(
        self,
        key: SandboxKey,
        result: PythonResult,
        *,
        now: datetime | None = None,
    ) -> PythonResult:
        timestamp = now or utc_now()
        row = await self._session.get(
            PythonExecutionRow,
            (key.workload_kind.value, key.logical_id, result.operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python execution intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        row.state = result.state.value
        row.stdout = result.stdout
        row.stderr = result.stderr
        row.result = result.result
        row.error_name = result.error_name
        row.error_message = result.error_message
        row.traceback = result.traceback
        row.output_truncated = result.output_truncated
        row.completed_at = timestamp
        row.updated_at = timestamp
        await self._session.flush()
        return self._python_result(row)

    async def mark_python_execution_unknown(
        self, key: SandboxKey, operation_id: UUID, *, now: datetime | None = None
    ) -> PythonResult:
        timestamp = now or utc_now()
        row = await self._session.get(
            PythonExecutionRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python execution intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state == PythonExecutionState.STARTING.value:
            row.state = PythonExecutionState.UNKNOWN.value
            row.updated_at = timestamp
            await self._session.flush()
        return self._python_result(row)

    async def reset_python_execution_after_rejection(
        self, key: SandboxKey, operation_id: UUID, *, now: datetime | None = None
    ) -> PythonResult:
        timestamp = now or utc_now()
        row = await self._session.get(
            PythonExecutionRow,
            (key.workload_kind.value, key.logical_id, operation_id),
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.UNKNOWN_DISPATCH,
                "Python execution intent does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        if row.state == PythonExecutionState.STARTING.value:
            row.state = PythonExecutionState.RESERVED.value
            row.updated_at = timestamp
            await self._session.flush()
        return self._python_result(row)

    async def _require_session_row(
        self, key: SandboxKey, session_id: UUID
    ) -> SessionRow:
        row = await self._session.get(
            SessionRow, (key.workload_kind.value, key.logical_id, session_id)
        )
        if row is None:
            raise AgentBoxError(
                ErrorCode.SANDBOX_NOT_FOUND,
                "Python session does not exist",
                retry=RetryDisposition.DO_NOT_RETRY,
                status_code=404,
            )
        return row

    async def get_allocation_by_id(
        self, allocation_id: UUID
    ) -> PhysicalAllocation | None:
        row = await self._session.get(AllocationRow, allocation_id)
        return self._allocation(row) if row is not None else None

    @staticmethod
    def _logical(row: LogicalSandboxRow) -> LogicalSandbox:
        return LogicalSandbox(
            key=SandboxKey(
                workload_kind=WorkloadKind(row.workload_kind),
                logical_id=row.logical_id,
            ),
            desired_state=SandboxDesiredState(row.desired_state),
            profile=SandboxProfileRef(
                name=row.profile_name,
                digest=row.profile_digest,
            ),
            current_allocation_id=row.current_allocation_id,
            allocation_epoch=row.allocation_epoch,
            last_used_at=_aware(row.last_used_at) or utc_now(),
            protected_until=_aware(row.protected_until),
            released_at=_aware(row.released_at),
            delete_after=_aware(row.delete_after),
        )

    @staticmethod
    def _allocation(row: AllocationRow) -> PhysicalAllocation:
        return PhysicalAllocation(
            allocation_id=row.allocation_id,
            key=SandboxKey(
                workload_kind=WorkloadKind(row.workload_kind),
                logical_id=row.logical_id,
            ),
            allocation_token=row.allocation_token,
            provider_name=row.provider_name,
            provider_scope=row.provider_scope,
            provider_id=row.provider_id,
            provider_instance_id=row.provider_instance_id,
            profile_name=row.profile_name,
            profile_digest=row.profile_digest,
            state=AllocationState(row.state),
            allocation_epoch=row.allocation_epoch,
            retry_after=_aware(row.retry_after),
        )

    @staticmethod
    def _process(row: ProcessIntentRow) -> ProcessIntent:
        return ProcessIntent(
            key=SandboxKey(
                workload_kind=WorkloadKind(row.workload_kind),
                logical_id=row.logical_id,
            ),
            operation_id=row.operation_id,
            allocation_id=row.allocation_id,
            allocation_epoch=row.allocation_epoch,
            request_hash=row.request_hash,
            state=ProcessState(row.state),
            provider_process_id=row.provider_process_id,
            provider_tag=row.provider_tag,
            cwd=row.cwd,
            tty=row.tty,
            output_limit_bytes=row.output_limit_bytes,
            deadline_at=_aware(row.deadline_at) or utc_now(),
            started_at=_aware(row.started_at),
            completed_at=_aware(row.completed_at),
            exit_code=row.exit_code,
        )

    @staticmethod
    def _python_session(row: SessionRow) -> PythonSessionRef:
        return PythonSessionRef(
            key=SandboxKey(
                workload_kind=WorkloadKind(row.workload_kind),
                logical_id=row.logical_id,
            ),
            session_id=row.session_id,
            allocation_id=row.allocation_id,
            allocation_epoch=row.allocation_epoch,
            provider_context_id=row.provider_context_id,
            cwd=row.cwd,
            environment_keys=tuple(row.env_keys),
            state=PythonSessionState(row.state),
        )

    @staticmethod
    def _python_result(row: PythonExecutionRow) -> PythonResult:
        return PythonResult(
            operation_id=row.operation_id,
            state=PythonExecutionState(row.state),
            stdout=row.stdout,
            stderr=row.stderr,
            result=row.result,
            error_name=row.error_name,
            error_message=row.error_message,
            traceback=row.traceback,
            output_truncated=row.output_truncated,
        )

    @staticmethod
    def _storage(row: WorkspaceStorageRow) -> WorkspaceStorage:
        if row.delete_token is None:  # pragma: no cover - storage invariant
            raise RuntimeError("workspace storage has no storage token")
        return WorkspaceStorage(
            key=SandboxKey(
                workload_kind=WorkloadKind(row.workload_kind),
                logical_id=row.logical_id,
            ),
            provider_name=row.provider_name,
            storage_kind=StorageKind(row.storage_kind),
            provider_storage_id=row.provider_storage_id,
            bound_allocation_id=row.bound_allocation_id,
            state=StorageState(row.state),
            content_generation=row.content_generation,
            storage_token=row.delete_token,
        )

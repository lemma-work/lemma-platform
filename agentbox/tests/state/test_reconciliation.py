from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from agentbox.domain import (
    AdmissionClass,
    AdmissionState,
    AgentBoxError,
    AllocationState,
    DispatchState,
    ProviderAdmissionPolicy,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.persistence.models import (
    AllocationRow,
    CreateAttemptRow,
    ProviderAdmissionRow,
)
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRequest,
    ProviderInventoryAllocation,
    ProviderMetadataEntry,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.reconciliation import AgentBoxReconciler


pytestmark = pytest.mark.asyncio


def profile() -> SandboxProfileRef:
    return SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}")


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class LostCreateResponseProvider:
    name = "fault-injection"
    scope = "fault-injection:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.create_calls: list[ProviderCreateRequest] = []
        self.inventory_calls = 0
        self.inventory_matches = 1
        self.destroyed: list[str] = []

    async def create(self, request: ProviderCreateRequest):
        assert self.database.active_units_of_work == 0
        self.create_calls.append(request)
        raise ProviderCreateAmbiguous("provider accepted create; response was lost")

    async def find_allocations(
        self,
        metadata: tuple[ProviderMetadataEntry, ...],
        *,
        deadline_at: datetime,
    ) -> tuple[ProviderInventoryAllocation, ...]:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.inventory_calls += 1
        expected = {item.name: item.value for item in metadata}
        if self.create_calls:
            assert expected["allocation-token"] == str(
                self.create_calls[0].allocation_token
            )
        return tuple(
            ProviderInventoryAllocation(
                provider_id=f"accepted-{index}",
                provider_instance_id=f"accepted-{index}",
                workspace_storage=ProviderStorageResult(
                    provider_storage_id="workspace-volume",
                    bound_to_allocation=False,
                ),
            )
            for index in range(self.inventory_matches)
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        assert self.database.active_units_of_work == 0
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def destroy_allocation(
        self, allocation: ProviderAllocationRef, *, deadline_at: datetime
    ) -> None:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.destroyed.append(allocation.provider_id)

    async def close(self) -> None:
        return None


async def make_ambiguous(
    database: StateDatabase,
    provider: LostCreateResponseProvider,
) -> tuple[SandboxLifecycleService, SandboxKey, datetime]:
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    with pytest.raises(AgentBoxError):
        await lifecycle.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
    token = provider.create_calls[0].allocation_token
    async with database.uow() as uow:
        await uow.repository.mark_create_unknown(
            token,
            reconcile_after=datetime.now(timezone.utc) - timedelta(seconds=1),
            error_code="AMBIGUOUS_CREATE",
        )
        await uow.commit()
    return lifecycle, key, deadline


async def test_lost_create_response_is_adopted_by_exact_metadata_without_recreate(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    lifecycle, key, deadline = await make_ambiguous(database, provider)

    reconciled = await AgentBoxReconciler(
        database, provider, create_absence_grace_seconds=30
    ).reconcile_once(deadline_at=deadline)
    handle = await lifecycle.inspect(key)

    assert reconciled == 1
    assert handle is not None
    assert handle.ready is True
    assert handle.allocation_state == AllocationState.ACTIVE
    assert len(provider.create_calls) == 1
    assert provider.inventory_calls == 1
    assert database.active_units_of_work == 0


async def test_inventory_absence_during_grace_never_blindly_recreates(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    provider.inventory_matches = 0
    lifecycle, key, deadline = await make_ambiguous(database, provider)

    await AgentBoxReconciler(
        database,
        provider,
        create_absence_grace_seconds=30,
        retry_seconds=10,
    ).reconcile_once(deadline_at=deadline)
    pending = await lifecycle.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert pending.allocation_state == AllocationState.UNKNOWN
    assert len(provider.create_calls) == 1
    assert provider.inventory_calls == 1


async def test_multiple_inventory_matches_remain_indeterminate(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    provider.inventory_matches = 2
    lifecycle, key, deadline = await make_ambiguous(database, provider)

    await AgentBoxReconciler(database, provider).reconcile_once(deadline_at=deadline)
    handle = await lifecycle.inspect(key)

    assert handle is not None
    assert handle.allocation_state == AllocationState.UNKNOWN
    assert len(provider.create_calls) == 1
    assert provider.destroyed == []


async def make_stale_dispatch(
    database: StateDatabase,
    provider: LostCreateResponseProvider,
    *,
    workload_kind: WorkloadKind,
) -> tuple[SandboxLifecycleService, SandboxKey, datetime]:
    key = SandboxKey(workload_kind, uuid4())
    selected_profile = profile()
    admission_class = AdmissionClass.INTERACTIVE
    if workload_kind == WorkloadKind.FUNCTION:
        selected_profile = SandboxProfileRef("function-python-v1", f"sha256:{'b' * 64}")
        admission_class = AdmissionClass.BATCH
    stale_at = datetime.now(timezone.utc) - timedelta(hours=1)
    async with database.uow() as uow:
        await uow.repository.ensure_logical(key, selected_profile)
        if workload_kind == WorkloadKind.WORKSPACE:
            await uow.repository.ensure_workspace_storage(
                key,
                provider_name=provider.name,
                storage_kind=provider.workspace_storage_kind,
            )
        intent = await uow.repository.begin_allocation(
            key,
            selected_profile,
            provider_name=provider.name,
            provider_scope=provider.scope,
            admission_class=admission_class.value,
            request_hash="f" * 64,
        )
        decision = await uow.repository.reserve_provider_capacity(
            intent.allocation.allocation_id,
            admission_class=admission_class,
            policy=ProviderAdmissionPolicy.permissive_for_tests(),
        )
        assert decision.accepted is True
        assert await uow.repository.mark_create_dispatched(
            intent.allocation.allocation_token,
            now=stale_at,
        )
        await uow.commit()
    return (
        SandboxLifecycleService(database, provider),
        key,
        datetime.now(timezone.utc) + timedelta(seconds=30),
    )


async def test_stale_dispatched_create_without_inventory_match_releases_capacity(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    provider.inventory_matches = 0
    lifecycle, key, deadline = await make_stale_dispatch(
        database,
        provider,
        workload_kind=WorkloadKind.FUNCTION,
    )

    reconciled = await AgentBoxReconciler(
        database,
        provider,
        create_absence_grace_seconds=1,
        dispatched_create_stale_seconds=30,
    ).reconcile_once(deadline_at=deadline)
    handle = await lifecycle.inspect(key)

    assert reconciled == 1
    assert handle is not None
    assert handle.allocation_state == AllocationState.ERROR
    async with database.engine.connect() as connection:
        allocation_token = await connection.scalar(
            select(AllocationRow.allocation_token).where(
                AllocationRow.logical_id == key.logical_id
            )
        )
        admission_state = await connection.scalar(
            select(AllocationRow.admission_state).where(
                AllocationRow.logical_id == key.logical_id
            )
        )
        attempt_state = await connection.scalar(
            select(CreateAttemptRow.dispatch_state).where(
                CreateAttemptRow.allocation_token == allocation_token
            )
        )
        reserved_count = await connection.scalar(
            select(ProviderAdmissionRow.reserved_count).where(
                ProviderAdmissionRow.provider_scope == provider.scope
            )
        )
    assert admission_state == AdmissionState.RELEASED.value
    assert attempt_state == DispatchState.RESOLVED.value
    assert reserved_count == 0


async def test_stale_dispatched_workspace_with_exact_match_is_adopted(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    lifecycle, key, deadline = await make_stale_dispatch(
        database,
        provider,
        workload_kind=WorkloadKind.WORKSPACE,
    )

    reconciled = await AgentBoxReconciler(
        database,
        provider,
        dispatched_create_stale_seconds=30,
    ).reconcile_once(deadline_at=deadline)
    handle = await lifecycle.inspect(key)

    assert reconciled == 1
    assert handle is not None
    assert handle.ready is True
    assert handle.allocation_state == AllocationState.ACTIVE


async def test_reconciliation_repairs_terminal_allocation_reservation(
    database: StateDatabase,
) -> None:
    provider = LostCreateResponseProvider(database)
    provider.inventory_matches = 0
    lifecycle, key, deadline = await make_stale_dispatch(
        database,
        provider,
        workload_kind=WorkloadKind.FUNCTION,
    )
    reconciler = AgentBoxReconciler(
        database,
        provider,
        create_absence_grace_seconds=1,
        dispatched_create_stale_seconds=30,
    )
    await reconciler.reconcile_once(deadline_at=deadline)

    async with database.engine.begin() as connection:
        await connection.execute(
            update(AllocationRow)
            .where(AllocationRow.logical_id == key.logical_id)
            .values(admission_state=AdmissionState.RESERVED.value)
        )
        await connection.execute(
            update(ProviderAdmissionRow)
            .where(ProviderAdmissionRow.provider_scope == provider.scope)
            .values(reserved_count=1)
        )

    assert await reconciler.reconcile_once(deadline_at=deadline) == 0
    handle = await lifecycle.inspect(key)
    assert handle is not None
    assert handle.allocation_state == AllocationState.ERROR
    async with database.engine.connect() as connection:
        admission_state = await connection.scalar(
            select(AllocationRow.admission_state).where(
                AllocationRow.logical_id == key.logical_id
            )
        )
        reserved_count = await connection.scalar(
            select(ProviderAdmissionRow.reserved_count).where(
                ProviderAdmissionRow.provider_scope == provider.scope
            )
        )
    assert admission_state == AdmissionState.RELEASED.value
    assert reserved_count == 0

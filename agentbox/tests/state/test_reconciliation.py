from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    AllocationState,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
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

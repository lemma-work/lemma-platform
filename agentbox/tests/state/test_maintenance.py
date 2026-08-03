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
    ErrorCode,
    SandboxDesiredState,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.maintenance import SandboxMaintenanceWorker
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderLifecycleError,
    ProviderReadyResult,
    ProviderStorageResult,
)


pytestmark = pytest.mark.asyncio


def _profile(kind: WorkloadKind) -> SandboxProfileRef:
    fill = "a" if kind == WorkloadKind.WORKSPACE else "b"
    return SandboxProfileRef(f"{kind.value}-python-v1", f"sha256:{fill * 64}")


class Provider:
    name = "fake"
    scope = "fake:maintenance"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.release_calls: list[str] = []
        self.destroy_calls: list[str] = []
        self.destroyed_storage: list[str] = []
        self.fail_release = False

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        provider_id = f"sandbox-{request.allocation_id}"
        return ProviderCreateResult(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            provider_request_id=None,
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=(
                        f"volume-{request.workspace_storage.storage_token}"
                    ),
                    bound_to_allocation=False,
                )
                if request.workspace_storage is not None
                else None
            ),
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        **_kwargs,
    ) -> ProviderReadyResult:
        assert self.database.active_units_of_work == 0
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        **_kwargs,
    ) -> None:
        assert self.database.active_units_of_work == 0
        self.release_calls.append(allocation.provider_id)
        if self.fail_release:
            raise ProviderLifecycleError("injected release failure")

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        **_kwargs,
    ) -> None:
        assert self.database.active_units_of_work == 0
        self.destroy_calls.append(allocation.provider_id)

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        **_kwargs,
    ) -> None:
        assert self.database.active_units_of_work == 0
        self.destroyed_storage.append(provider_storage_id)


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


async def _ensure(
    lifecycle: SandboxLifecycleService,
    key: SandboxKey,
) -> None:
    await lifecycle.ensure(
        key,
        _profile(key.workload_kind),
        admission_class=(
            AdmissionClass.INTERACTIVE
            if key.workload_kind == WorkloadKind.WORKSPACE
            else AdmissionClass.LATENCY
        ),
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )


async def test_workspace_without_a_disk_adopts_the_new_profile(
    database: StateDatabase,
) -> None:
    """Profile drift tolerance only protects a workspace that holds files.

    A workspace keeps its existing profile while it owns a disk, but once
    retention expiry has removed the allocation and its storage there is
    nothing left to preserve, so the next ensure adopts the newly shipped
    profile and the workspace catches up.
    """

    provider = Provider(database)
    lifecycle = SandboxLifecycleService(
        database,
        provider,
        workspace_retention_seconds=0,
    )
    worker = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    replacement = SandboxProfileRef("workspace-python-v2", f"sha256:{'d' * 64}")

    await _ensure(lifecycle, key)
    # Release, then hard retention expiry: the disk is gone.
    assert await worker.run_once(deadline_at=deadline) == 1
    assert await worker.run_once(deadline_at=deadline) == 1

    handle = await lifecycle.ensure(
        key,
        replacement,
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert handle.ready is True
    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        await uow.commit()
    assert logical is not None
    assert logical.profile == replacement


async def test_workspace_release_then_hard_expiry_recreates_fresh(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    lifecycle = SandboxLifecycleService(
        database,
        provider,
        workspace_retention_seconds=0,
    )
    worker = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await _ensure(lifecycle, key)

    first = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    async with database.uow() as uow:
        released = await uow.repository.get_logical(key)
        allocation = await uow.repository.current_allocation(key)
        await uow.commit()

    assert first == 1
    assert released is not None
    assert released.desired_state == SandboxDesiredState.RELEASED
    assert allocation is not None
    assert allocation.state == AllocationState.RELEASED
    assert len(provider.release_calls) == 1

    second = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    async with database.uow() as uow:
        deleted = await uow.repository.get_logical(key)
        storage = await uow.repository.get_workspace_storage(key)
        await uow.commit()

    assert second == 1
    assert deleted is not None
    assert deleted.desired_state == SandboxDesiredState.RELEASED
    assert deleted.current_allocation_id is None
    assert storage is not None and storage.provider_storage_id is None
    assert len(provider.destroy_calls) == 1
    assert len(provider.destroyed_storage) == 1

    await _ensure(lifecycle, key)
    async with database.uow() as uow:
        recreated = await uow.repository.get_logical(key)
        fresh_storage = await uow.repository.get_workspace_storage(key)
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()

    assert recreated is not None
    assert recreated.desired_state == SandboxDesiredState.PRESENT
    assert recreated.current_allocation_id is not None
    assert fresh_storage is not None
    assert fresh_storage.provider_storage_id is not None
    assert fresh_storage.content_generation == 1
    assert len(allocations) == 2
    assert allocations[0].state == AllocationState.DESTROYED
    assert allocations[1].state == AllocationState.ACTIVE


async def test_workspace_hard_expiry_is_measured_from_last_activity(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    lifecycle = SandboxLifecycleService(
        database,
        provider,
        workspace_retention_seconds=48 * 60 * 60,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await _ensure(lifecycle, key)
    async with database.uow() as uow:
        before_release = await uow.repository.get_logical(key)
        await uow.commit()
    assert before_release is not None
    paused_at = before_release.last_used_at + timedelta(minutes=5)

    async with database.uow() as uow:
        _claim, released, _allocation = await uow.repository.begin_release(
            key,
            claimed_until=paused_at + timedelta(seconds=30),
            retention_seconds=48 * 60 * 60,
            now=paused_at,
        )
        await uow.commit()

    assert released.delete_after == before_release.last_used_at + timedelta(hours=48)


async def test_activity_cannot_start_after_maintenance_claim(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    lifecycle = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await _ensure(lifecycle, key)
    now = datetime.now(timezone.utc)

    async with database.uow() as uow:
        await uow.repository.begin_release(
            key,
            claimed_until=now + timedelta(seconds=30),
            retention_seconds=48 * 60 * 60,
            now=now,
        )
        with pytest.raises(AgentBoxError) as raised:
            await uow.repository.protect_activity(
                key,
                until=now + timedelta(seconds=20),
                now=now,
            )
        await uow.rollback()

    assert raised.value.code == ErrorCode.SANDBOX_QUIESCING


async def test_idle_function_compute_is_destroyed_and_can_be_recreated(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    lifecycle = SandboxLifecycleService(database, provider)
    worker = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    await _ensure(lifecycle, key)

    completed = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        await uow.commit()

    assert completed == 1
    assert logical is not None
    assert logical.desired_state == SandboxDesiredState.RELEASED
    assert logical.current_allocation_id is None
    assert provider.release_calls == []
    assert len(provider.destroy_calls) == 1

    await _ensure(lifecycle, key)
    async with database.uow() as uow:
        recreated = await uow.repository.get_logical(key)
        await uow.commit()
    assert recreated is not None
    assert recreated.desired_state == SandboxDesiredState.PRESENT
    assert recreated.current_allocation_id is not None


async def test_active_runtime_lease_blocks_idle_function_cleanup(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    lifecycle = SandboxLifecycleService(database, provider)
    worker = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
    )
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    await _ensure(lifecycle, key)
    lease_deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    async with database.uow() as uow:
        await uow.repository.protect_activity(key, until=lease_deadline)
        await uow.commit()

    completed = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert completed == 0
    assert provider.destroy_calls == []


async def test_failed_provider_release_keeps_claim_and_prevents_blind_duplicate(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    provider.fail_release = True
    lifecycle = SandboxLifecycleService(database, provider)
    worker = SandboxMaintenanceWorker(
        database,
        lifecycle,
        workspace_idle_seconds=0,
        function_idle_seconds=0,
        batch_size=1,
    )
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    await _ensure(lifecycle, key)

    first = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )
    second = await worker.run_once(
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30)
    )

    assert first == 0
    assert second == 0
    assert len(provider.release_calls) == 1
    assert database.active_units_of_work == 0

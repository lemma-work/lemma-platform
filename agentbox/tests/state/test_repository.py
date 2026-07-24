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
    ProcessState,
    RetryDisposition,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    StorageState,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.ports import (
    ProviderAllocationFailed,
    ProviderAllocationRef,
    ProviderCreateAmbiguous,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderNotReady,
    ProviderLifecycleError,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.persistence.uow import StateDatabase
from agentbox.reconciliation import AgentBoxReconciler


pytestmark = pytest.mark.asyncio


def profile(name: str = "workspace-python-v1", fill: str = "a") -> SandboxProfileRef:
    return SandboxProfileRef(name=name, digest=f"sha256:{fill * 64}")


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class FakeProvider:
    name = "fake"
    scope = "fake:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self.database = database
        self.create_calls: list[ProviderCreateRequest] = []
        self.ready_calls = 0

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        self.create_calls.append(request)
        provider_id = f"provider-{len(self.create_calls)}"
        return ProviderCreateResult(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            provider_request_id=f"request-{len(self.create_calls)}",
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=(
                        request.workspace_storage.provider_storage_id
                        or f"volume-{request.workspace_storage.storage_token}"
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
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        assert self.database.active_units_of_work == 0
        self.ready_calls += 1
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def close(self) -> None:
        return None


class AmbiguousProvider(FakeProvider):
    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        self.create_calls.append(request)
        raise ProviderCreateAmbiguous("response lost after provider acceptance")


class InitiallyNotReadyProvider(FakeProvider):
    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        assert self.database.active_units_of_work == 0
        self.ready_calls += 1
        if self.ready_calls == 1:
            raise ProviderNotReady("container is still starting", retry_after_ms=10)
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )


class FailedReadinessProvider(FakeProvider):
    def __init__(self, database: StateDatabase, *, destroy_fails: bool = False) -> None:
        super().__init__(database)
        self.destroy_fails = destroy_fails
        self.destroy_calls: list[ProviderAllocationRef] = []

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del allocation, profile, deadline_at
        assert self.database.active_units_of_work == 0
        raise ProviderAllocationFailed("sandbox boot failed")

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.destroy_calls.append(allocation)
        if self.destroy_fails:
            raise ProviderLifecycleError("provider unavailable during cleanup")


class MissingNativeWorkspaceProvider(FakeProvider):
    workspace_storage_kind = StorageKind.SANDBOX_NATIVE

    def __init__(self, database: StateDatabase) -> None:
        super().__init__(database)
        self.destroy_calls: list[ProviderAllocationRef] = []

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        self.create_calls.append(request)
        provider_id = f"sandbox-{len(self.create_calls)}"
        return ProviderCreateResult(
            provider_id=provider_id,
            provider_instance_id=provider_id,
            provider_request_id=None,
            workspace_storage=ProviderStorageResult(
                provider_storage_id=provider_id,
                bound_to_allocation=True,
            ),
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
        self.ready_calls += 1
        if self.ready_calls == 1:
            raise ProviderAllocationFailed("sandbox no longer exists")
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.destroy_calls.append(allocation)


async def test_logical_namespace_is_composite(database: StateDatabase):
    logical_id = uuid4()
    workspace = SandboxKey(WorkloadKind.WORKSPACE, logical_id)
    function = SandboxKey(WorkloadKind.FUNCTION, logical_id)

    async with database.uow() as uow:
        workspace_record = await uow.repository.ensure_logical(
            workspace, profile("workspace-python-v1", "a")
        )
        function_record = await uow.repository.ensure_logical(
            function, profile("function-python-v1", "b")
        )
        await uow.commit()

    assert workspace_record.key != function_record.key
    assert workspace_record.profile.name == "workspace-python-v1"
    assert function_record.profile.name == "function-python-v1"


async def test_ensure_commits_before_provider_io_and_creates_once(
    database: StateDatabase,
):
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    first = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    second = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert first.ready is True
    assert second.ready is True
    assert first.allocation_id == second.allocation_id
    assert len(provider.create_calls) == 1
    assert provider.ready_calls == 1
    assert database.active_units_of_work == 0
    async with database.uow() as uow:
        storage = await uow.repository.get_workspace_storage(key)
        await uow.commit()
    assert storage is not None
    assert storage.state == StorageState.READY
    assert storage.provider_storage_id is not None


async def test_lost_create_response_is_not_replayed(database: StateDatabase):
    provider = AmbiguousProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )
    assert raised.value.code == ErrorCode.AMBIGUOUS_CREATE

    pending = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    assert pending.ready is False
    assert pending.allocation_state == AllocationState.UNKNOWN
    assert len(provider.create_calls) == 1


async def test_acknowledged_create_resumes_readiness_without_recreate(
    database: StateDatabase,
):
    provider = InitiallyNotReadyProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    pending = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    ready = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert pending.ready is False
    assert pending.allocation_state == AllocationState.PROVISIONING
    assert pending.retry_after_ms is not None
    assert ready.ready is True
    assert ready.allocation_id == pending.allocation_id
    assert len(provider.create_calls) == 1
    assert provider.ready_calls == 2
    assert database.active_units_of_work == 0


async def test_failed_readiness_destroys_exact_allocation_before_releasing_state(
    database: StateDatabase,
):
    provider = FailedReadinessProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )

    assert raised.value.retry == RetryDisposition.SAFE_SAME_OPERATION
    assert len(provider.destroy_calls) == 1
    assert provider.destroy_calls[0].provider_id == "provider-1"
    async with database.uow() as uow:
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert allocations[0].state == AllocationState.ERROR


async def test_failed_readiness_cleanup_failure_remains_reconcilable(
    database: StateDatabase,
):
    provider = FailedReadinessProvider(database, destroy_fails=True)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )

    assert raised.value.retry == RetryDisposition.WAIT
    assert raised.value.retry_after_ms == 1000
    async with database.uow() as uow:
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert allocations[0].state == AllocationState.PROVISIONING
    assert allocations[0].retry_after is not None

    provider.destroy_fails = False
    async with database.uow() as uow:
        await uow.repository.mark_allocation_provisioning_retry(
            allocations[0].allocation_id,
            retry_after=datetime.now(timezone.utc) - timedelta(seconds=1),
            error_code=ErrorCode.PROVIDER_UNAVAILABLE.value,
        )
        await uow.commit()
    reconciled = await AgentBoxReconciler(database, provider).reconcile_once(
        deadline_at=deadline
    )
    async with database.uow() as uow:
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()

    assert reconciled == 1
    assert len(provider.destroy_calls) == 2
    assert allocations[0].state == AllocationState.ERROR


async def test_missing_sandbox_native_workspace_rebinds_to_new_allocation(
    database: StateDatabase,
):
    provider = MissingNativeWorkspaceProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )

    assert raised.value.retry == RetryDisposition.SAFE_SAME_OPERATION
    recovered = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert recovered.ready is True
    assert len(provider.create_calls) == 2
    assert [item.provider_id for item in provider.destroy_calls] == ["sandbox-1"]
    async with database.uow() as uow:
        storage = await uow.repository.get_workspace_storage(key)
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert storage is not None
    assert storage.provider_storage_id == "sandbox-2"
    assert storage.bound_allocation_id == recovered.allocation_id
    assert [item.state for item in allocations] == [
        AllocationState.ERROR,
        AllocationState.ACTIVE,
    ]
    assert allocations[1].provider_id == "sandbox-2"


async def test_active_sandbox_native_workspace_cannot_be_rebound(
    database: StateDatabase,
):
    provider = MissingNativeWorkspaceProvider(database)
    provider.ready_calls = 1
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    active = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    async with database.uow() as uow:
        with pytest.raises(AgentBoxError) as raised:
            await uow.repository.bind_workspace_storage(
                key,
                provider_storage_id="sandbox-other",
                allocation_id=uuid4(),
            )
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT
    assert active.ready is True


async def test_profile_change_replaces_and_fences_allocation(
    database: StateDatabase,
):
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    first = await service.ensure(
        key,
        profile(fill="a"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    second = await service.ensure(
        key,
        profile(name="workspace-python-v7", fill="b"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert first.allocation_epoch == 1
    assert second.allocation_epoch == 2
    assert first.allocation_id != second.allocation_id
    async with database.uow() as uow:
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert [item.state for item in allocations] == [
        AllocationState.DRAINING,
        AllocationState.ACTIVE,
    ]


async def test_process_operation_id_is_typed_and_deduplicated(
    database: StateDatabase,
):
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await service.ensure(
        key,
        profile(name="function-python-v1", fill="b"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=deadline,
    )
    operation_id = uuid4()

    async with database.uow() as uow:
        first, first_created = await uow.repository.reserve_process(
            key,
            operation_id=operation_id,
            request_hash="1" * 64,
            env_keys=("ATTEMPT_TICKET",),
            cwd="/tmp",
            tty=False,
            output_limit_bytes=1024 * 1024,
            deadline_at=deadline,
        )
        second, second_created = await uow.repository.reserve_process(
            key,
            operation_id=operation_id,
            request_hash="1" * 64,
            env_keys=("ATTEMPT_TICKET",),
            cwd="/tmp",
            tty=False,
            output_limit_bytes=1024 * 1024,
            deadline_at=deadline,
        )
        await uow.commit()

    assert first == second
    assert first.state == ProcessState.RESERVED
    assert first_created is True
    assert second_created is False

    async with database.uow() as uow:
        with pytest.raises(AgentBoxError) as raised:
            await uow.repository.reserve_process(
                key,
                operation_id=operation_id,
                request_hash="2" * 64,
                env_keys=("ATTEMPT_TICKET",),
                cwd="/tmp",
                tty=False,
                output_limit_bytes=1024 * 1024,
                deadline_at=deadline,
            )
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT


async def test_uncommitted_unit_of_work_rolls_back(database: StateDatabase):
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    async with database.uow() as uow:
        await uow.repository.ensure_logical(key, profile())

    async with database.uow() as uow:
        record = await uow.repository.get_logical(key)
        await uow.commit()
    assert record is None

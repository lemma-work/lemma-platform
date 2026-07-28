from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    AllocationState,
    DispatchState,
    ErrorCode,
    ProviderAdmissionPolicy,
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
from agentbox.persistence.repository import AgentBoxRepository
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


class CancelledCreateProvider(FakeProvider):
    def __init__(self, database: StateDatabase) -> None:
        super().__init__(database)
        self.started = asyncio.Event()

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        assert self.database.active_units_of_work == 0
        self.create_calls.append(request)
        self.started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


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
        self.release_calls: list[ProviderAllocationRef] = []

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

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del deadline_at
        assert self.database.active_units_of_work == 0
        self.release_calls.append(allocation)


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


async def test_destroy_generation_fences_late_create_acknowledgement(
    database: StateDatabase,
):
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    async with database.uow() as uow:
        await uow.repository.ensure_logical(
            key, profile("function-python-v1", "a")
        )
        intent = await uow.repository.begin_allocation(
            key,
            profile("function-python-v1", "a"),
            provider_name="fake",
            provider_scope="fake:test",
            admission_class=AdmissionClass.INTERACTIVE.value,
            request_hash="a" * 64,
        )
        decision = await uow.repository.reserve_provider_capacity(
            intent.allocation.allocation_id,
            admission_class=AdmissionClass.INTERACTIVE,
            policy=ProviderAdmissionPolicy.permissive_for_tests(),
        )
        assert decision.accepted
        assert await uow.repository.mark_create_dispatched(
            intent.allocation.allocation_token,
            expected_resource_generation=intent.allocation.resource_generation,
        )
        await uow.commit()

    async with database.uow() as uow:
        await uow.repository.begin_destroy(key, claimed_until=deadline)
        await uow.commit()

    async with database.uow() as uow:
        with pytest.raises(AgentBoxError) as raised:
            await uow.repository.acknowledge_create(
                intent.allocation.allocation_token,
                provider_id="late-provider-object",
                expected_resource_generation=intent.allocation.resource_generation,
                provider_instance_id="late-provider-object",
            )
        await uow.rollback()

    assert raised.value.code == ErrorCode.ALLOCATION_CHANGED
    async with database.uow() as uow:
        logical = await uow.repository.get_logical(key)
        allocation = await uow.repository.get_allocation_by_token(
            intent.allocation.allocation_token
        )
        await uow.commit()
    assert logical is not None
    assert allocation is not None
    assert logical.resource_generation > allocation.resource_generation
    assert allocation.provider_id is None


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


async def test_verified_ensure_rechecks_active_without_changing_epoch(
    database: StateDatabase,
):
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    created = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    verified = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
        verify_ready=True,
    )

    assert verified.ready is True
    assert verified.allocation_id == created.allocation_id
    assert verified.allocation_epoch == created.allocation_epoch
    assert len(provider.create_calls) == 1
    assert provider.ready_calls == 2


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


async def test_repeatedly_cancelled_create_becomes_durably_reconcilable(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CancelledCreateProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    durable_write_started = asyncio.Event()
    allow_durable_write = asyncio.Event()
    original = AgentBoxRepository.mark_create_unknown

    async def delay_durable_write(
        repository: AgentBoxRepository,
        allocation_token,
        *,
        reconcile_after,
        error_code,
        expected_resource_generation=None,
        now=None,
    ) -> None:
        durable_write_started.set()
        await allow_durable_write.wait()
        await original(
            repository,
            allocation_token,
            reconcile_after=reconcile_after,
            error_code=error_code,
            expected_resource_generation=expected_resource_generation,
            now=now,
        )

    monkeypatch.setattr(
        AgentBoxRepository,
        "mark_create_unknown",
        delay_durable_write,
    )
    task = asyncio.create_task(
        service.ensure(
            key,
            profile("function-python-v1", "b"),
            admission_class=AdmissionClass.BATCH,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
    )

    await provider.started.wait()
    task.cancel()
    await durable_write_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    allow_durable_write.set()
    with pytest.raises(asyncio.CancelledError):
        unexpected = await task
        pytest.fail(f"cancelled ensure unexpectedly returned {unexpected!r}")

    pending = await service.inspect(key)
    assert pending is not None
    assert pending.allocation_state is None
    async with database.uow() as uow:
        attempts = await uow.repository.list_allocations(key)
        await uow.commit()
    assert [item.state for item in attempts] == [AllocationState.UNKNOWN]
    assert len(provider.create_calls) == 1
    assert database.active_units_of_work == 0


async def test_cancelled_dispatch_transaction_releases_capacity(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    original = AgentBoxRepository.mark_create_dispatched

    async def cancel_after_dispatch(
        repository: AgentBoxRepository,
        allocation_token,
        *,
        expected_resource_generation,
        now=None,
    ) -> bool:
        dispatched = await original(
            repository,
            allocation_token,
            expected_resource_generation=expected_resource_generation,
            now=now,
        )
        current_task = asyncio.current_task()
        assert current_task is not None
        current_task.cancel()
        return dispatched

    monkeypatch.setattr(
        AgentBoxRepository,
        "mark_create_dispatched",
        cancel_after_dispatch,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.ensure(
            key,
            profile("function-python-v1", "b"),
            admission_class=AdmissionClass.BATCH,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    failed = await service.inspect(key)
    assert failed is not None
    assert failed.allocation_state is None
    async with database.uow() as uow:
        attempts = await uow.repository.list_allocations(key)
        await uow.commit()
    assert [item.state for item in attempts] == [AllocationState.ERROR]
    assert provider.create_calls == []
    assert database.active_units_of_work == 0


async def test_predispatch_generation_race_is_safe_to_retry(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    original = AgentBoxRepository.mark_create_dispatched

    async def release_before_dispatch(
        repository: AgentBoxRepository,
        allocation_token,
        *,
        expected_resource_generation,
        now=None,
    ) -> bool:
        timestamp = now or datetime.now(timezone.utc)
        await repository.begin_release(
            key,
            claimed_until=timestamp + timedelta(seconds=30),
            retention_seconds=0,
            now=timestamp,
        )
        return await original(
            repository,
            allocation_token,
            expected_resource_generation=expected_resource_generation,
            now=timestamp,
        )

    monkeypatch.setattr(
        AgentBoxRepository,
        "mark_create_dispatched",
        release_before_dispatch,
    )

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile("function-python-v1", "b"),
            admission_class=AdmissionClass.BATCH,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    assert raised.value.code == ErrorCode.ALLOCATION_CHANGED
    assert raised.value.retry == RetryDisposition.SAFE_SAME_OPERATION
    assert raised.value.retry_after_ms == 250
    assert provider.create_calls == []
    async with database.uow() as uow:
        attempts = await uow.repository.list_allocations(key)
        active, reserved = await uow.repository._admission_counts(provider.scope)
        await uow.commit()
    assert [item.state for item in attempts] == [AllocationState.ERROR]
    assert (active, reserved) == (0, 0)
    assert database.active_units_of_work == 0


async def test_cancelled_admission_transaction_does_not_leak_capacity(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    original = AgentBoxRepository.reserve_provider_capacity

    async def cancel_after_reservation(
        repository: AgentBoxRepository,
        allocation_id,
        *,
        admission_class,
        policy,
        now=None,
    ):
        decision = await original(
            repository,
            allocation_id,
            admission_class=admission_class,
            policy=policy,
            now=now,
        )
        current_task = asyncio.current_task()
        assert current_task is not None
        current_task.cancel()
        return decision

    monkeypatch.setattr(
        AgentBoxRepository,
        "reserve_provider_capacity",
        cancel_after_reservation,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.ensure(
            key,
            profile("function-python-v1", "b"),
            admission_class=AdmissionClass.BATCH,
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    allocation = await service.inspect(key)
    if allocation is not None:
        assert allocation.allocation_state is None
    async with database.uow() as uow:
        active, reserved = await uow.repository._admission_counts(provider.scope)
        await uow.commit()
    assert (active, reserved) == (0, 0)
    assert provider.create_calls == []
    assert database.active_units_of_work == 0


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


async def test_released_workspace_resume_is_durably_reconcilable(
    database: StateDatabase,
) -> None:
    provider = FakeProvider(database)
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    selected_profile = profile()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    created = await service.ensure(
        key,
        selected_profile,
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    assert created.allocation_id is not None

    release_at = datetime.now(timezone.utc)
    async with database.uow() as uow:
        claim, _, allocation = await uow.repository.begin_release(
            key,
            claimed_until=deadline,
            retention_seconds=3600,
            now=release_at,
        )
        assert allocation is not None
        await uow.repository.complete_release(
            key,
            allocation.allocation_id,
            claim_token=claim.token,
            now=release_at,
        )
        resumed = await uow.repository.resume_released_allocation(
            key,
            selected_profile,
            now=release_at + timedelta(seconds=1),
        )
        candidates = await uow.repository.list_due_create_reconciliation(
            provider.scope,
            stale_reserved_before=release_at,
            stale_dispatched_before=release_at,
            now=release_at + timedelta(seconds=2),
        )
        await uow.commit()

    assert resumed is not None
    assert resumed.state == AllocationState.PROVISIONING
    assert len(candidates) == 1
    assert candidates[0].dispatch_state == DispatchState.ACKNOWLEDGED
    assert candidates[0].allocation.allocation_id == created.allocation_id


async def test_destroy_generation_fence_prevents_reserved_create_dispatch(
    database: StateDatabase,
) -> None:
    provider = FakeProvider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    selected_profile = profile("function-python-v1", "b")
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    async with database.uow() as uow:
        await uow.repository.ensure_logical(key, selected_profile)
        intent = await uow.repository.begin_allocation(
            key,
            selected_profile,
            provider_name=provider.name,
            provider_scope=provider.scope,
            admission_class=AdmissionClass.BATCH.value,
            request_hash="d" * 64,
        )
        assert (
            await uow.repository.reserve_provider_capacity(
                intent.allocation.allocation_id,
                admission_class=AdmissionClass.BATCH,
                policy=ProviderAdmissionPolicy.permissive_for_tests(),
            )
        ).accepted
        await uow.repository.begin_destroy(
            key,
            claimed_until=deadline,
        )
        with pytest.raises(AgentBoxError) as stale:
            await uow.repository.mark_create_dispatched(
                intent.allocation.allocation_token,
                expected_resource_generation=intent.allocation.resource_generation,
            )
        await uow.commit()

    assert stale.value.code == ErrorCode.ALLOCATION_CHANGED
    async with database.uow() as uow:
        allocations = await uow.repository.list_allocations(key)
        active, reserved = await uow.repository._admission_counts(provider.scope)
        await uow.commit()
    assert [allocation.state for allocation in allocations] == [
        AllocationState.ERROR
    ]
    assert (active, reserved) == (0, 0)
    assert provider.create_calls == []


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
            expected_resource_generation=allocations[0].resource_generation,
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


async def test_storage_bind_conflict_fences_new_provider_and_returns_retryable_error(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
):
    provider = MissingNativeWorkspaceProvider(database)
    provider.ready_calls = 1
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    async def conflict(*_args, **_kwargs):
        raise AgentBoxError(
            ErrorCode.OPERATION_CONFLICT,
            "workspace storage is already bound to another provider resource",
            retry=RetryDisposition.DO_NOT_RETRY,
            status_code=409,
        )

    monkeypatch.setattr(AgentBoxRepository, "bind_workspace_storage", conflict)

    with pytest.raises(AgentBoxError) as raised:
        await service.ensure(
            key,
            profile(),
            admission_class=AdmissionClass.INTERACTIVE,
            deadline_at=deadline,
        )

    assert raised.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert raised.value.retry == RetryDisposition.SAFE_SAME_OPERATION
    assert "already bound" not in str(raised.value)
    assert [item.provider_id for item in provider.destroy_calls] == ["sandbox-1"]


async def test_released_native_workspace_with_new_profile_is_retired_and_rebound(
    database: StateDatabase,
):
    provider = MissingNativeWorkspaceProvider(database)
    provider.ready_calls = 1
    service = SandboxLifecycleService(database, provider)
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)

    first = await service.ensure(
        key,
        profile(),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    await service.release(key, deadline_at=deadline)
    second = await service.ensure(
        key,
        profile(name="workspace-python-v2", fill="b"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert second.ready is True
    assert second.allocation_id != first.allocation_id
    assert [item.provider_id for item in provider.release_calls] == ["sandbox-1"]
    assert [item.provider_id for item in provider.destroy_calls] == ["sandbox-1"]
    async with database.uow() as uow:
        storage = await uow.repository.get_workspace_storage(key)
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert storage is not None
    assert storage.provider_storage_id == "sandbox-2"
    assert storage.bound_allocation_id == second.allocation_id
    assert storage.content_generation == 1
    assert [item.state for item in allocations] == [
        AllocationState.ERROR,
        AllocationState.ACTIVE,
    ]


async def test_active_native_workspace_with_new_profile_is_fenced_and_rebound(
    database: StateDatabase,
):
    provider = MissingNativeWorkspaceProvider(database)
    provider.ready_calls = 1
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
        profile(name="workspace-python-v2", fill="b"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )

    assert second.ready is True
    assert second.allocation_id != first.allocation_id
    assert [item.provider_id for item in provider.release_calls] == ["sandbox-1"]
    assert [item.provider_id for item in provider.destroy_calls] == ["sandbox-1"]
    async with database.uow() as uow:
        storage = await uow.repository.get_workspace_storage(key)
        allocations = await uow.repository.list_allocations(key)
        await uow.commit()
    assert storage is not None
    assert storage.provider_storage_id == "sandbox-2"
    assert storage.content_generation == 1
    assert [item.state for item in allocations] == [
        AllocationState.ERROR,
        AllocationState.ACTIVE,
    ]


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


async def test_uncommitted_unit_of_work_rolls_back(database: StateDatabase):
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    async with database.uow() as uow:
        await uow.repository.ensure_logical(key, profile())

    async with database.uow() as uow:
        record = await uow.repository.get_logical(key)
        await uow.commit()
    assert record is None

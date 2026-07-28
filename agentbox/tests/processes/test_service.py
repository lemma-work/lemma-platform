from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    EnvironmentVariable,
    ErrorCode,
    ProcessState,
    ProcessOutputSnapshot,
    SandboxKey,
    SandboxProfileRef,
    StartProcessRequest,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderProcessStartAmbiguous,
    ProviderProcessMissing,
    ProviderProcessStartRejected,
    ProviderProcessStartRequest,
    ProviderProcessStartResult,
    ProviderReadyResult,
)
from agentbox.processes import ProcessExecutionService


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class LifecycleProvider:
    name = "fake"
    scope = "fake:test"
    workspace_storage_kind = StorageKind.VOLUME

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        return ProviderCreateResult(
            provider_id=f"sandbox-{request.allocation_id}",
            provider_instance_id=f"sandbox-{request.allocation_id}",
            provider_request_id=None,
            workspace_storage=None,
        )

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult:
        del profile, deadline_at
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def close(self) -> None:
        return None


class ProcessProvider:
    def __init__(
        self,
        database: StateDatabase,
        *,
        ambiguous: bool = False,
        reject_once: bool = False,
    ) -> None:
        self._database = database
        self._ambiguous = ambiguous
        self._reject_once = reject_once
        self.calls: list[ProviderProcessStartRequest] = []

    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        assert self._database.active_units_of_work == 0
        self.calls.append(request)
        if self._ambiguous:
            raise ProviderProcessStartAmbiguous("response lost")
        if self._reject_once and len(self.calls) == 1:
            raise ProviderProcessStartRejected("not dispatched")
        return ProviderProcessStartResult(
            provider_process_id=f"process-{request.process.operation_id}",
            provider_tag=str(request.process.operation_id),
        )

    async def terminate_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        del allocation, process, grace_seconds, deadline_at
        assert self._database.active_units_of_work == 0

    async def send_process_input(
        self,
        allocation: ProviderAllocationRef,
        *,
        process,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        del allocation, process, data, deadline_at
        assert self._database.active_units_of_work == 0

    async def read_process_output(
        self,
        allocation: ProviderAllocationRef,
        *,
        process,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        del allocation, process, after_sequence, wait_seconds, deadline_at
        assert self._database.active_units_of_work == 0
        return ProcessOutputSnapshot(
            chunks=(),
            next_sequence=1,
            truncated_before_sequence=None,
            state=ProcessState.FAILED,
            exit_code=137,
        )


class BlockingProcessProvider(ProcessProvider):
    def __init__(self, database: StateDatabase) -> None:
        super().__init__(database)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult:
        self.calls.append(request)
        self.started.set()
        await self.release.wait()
        return ProviderProcessStartResult(
            provider_process_id=f"process-{request.process.operation_id}",
            provider_tag=str(request.process.operation_id),
        )


async def provision_function(database: StateDatabase) -> SandboxKey:
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    lifecycle = SandboxLifecycleService(database, LifecycleProvider())
    await lifecycle.ensure(
        key,
        SandboxProfileRef("function-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    return key


def request(operation_id, *, command: str = "echo ok", token: str = "secret"):
    return StartProcessRequest(
        operation_id=operation_id,
        shell_command=command,
        argv=None,
        cwd="/tmp",
        environment=(EnvironmentVariable("ATTEMPT_TICKET", token),),
        tty=None,
        output_limit_bytes=1024,
        deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
    )


async def test_process_start_is_incarnation_local_deduplicated_and_outside_uow(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = ProcessProvider(database)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()

    original = request(operation_id)
    first, first_created = await service.start(key, original)
    second, second_created = await service.start(key, original)

    assert first.state == ProcessState.RUNNING
    assert second == first
    assert first_created is True
    assert second_created is False
    assert len(provider.calls) == 1
    assert database.active_units_of_work == 0

    with pytest.raises(AgentBoxError) as raised:
        await service.start(key, replace(original, shell_command="echo changed"))
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT

    with pytest.raises(AgentBoxError) as raised:
        await service.start(
            key,
            replace(
                original,
                environment=(EnvironmentVariable("ATTEMPT_TICKET", "rotated-secret"),),
            ),
        )
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT


async def test_ambiguous_process_start_is_not_replayed(database: StateDatabase):
    key = await provision_function(database)
    provider = ProcessProvider(database, ambiguous=True)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()

    original = request(operation_id)
    with pytest.raises(AgentBoxError) as raised:
        await service.start(key, original)
    with pytest.raises(AgentBoxError) as repeated:
        await service.start(key, original)

    assert raised.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert repeated.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert raised.value.retry.value == "do_not_retry"
    assert len(provider.calls) == 1


async def test_conflicting_inflight_process_request_does_not_coalesce(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = BlockingProcessProvider(database)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()
    original = request(operation_id)
    first = asyncio.create_task(service.start(key, original))
    await provider.started.wait()

    with pytest.raises(AgentBoxError) as raised:
        await service.start(
            key,
            replace(original, shell_command="echo conflicting"),
        )

    provider.release.set()
    await first
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT
    assert len(provider.calls) == 1


async def test_cancelled_process_waiter_does_not_leak_inflight_capacity(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = BlockingProcessProvider(database)
    service = ProcessExecutionService(database, provider)
    pending = asyncio.create_task(service.start(key, request(uuid4())))
    await provider.started.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    provider.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert service._inflight == {}


async def test_live_process_record_is_never_evicted_and_replayed(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = ProcessProvider(database)
    service = ProcessExecutionService(database, provider, max_records=1)
    operation_id = uuid4()
    original = request(operation_id)
    first, _ = await service.start(key, original)

    with pytest.raises(AgentBoxError) as full:
        await service.start(key, request(uuid4()))
    repeated, created = await service.start(key, original)

    assert full.value.code == ErrorCode.CAPACITY_EXHAUSTED
    assert repeated == first
    assert created is False
    assert len(provider.calls) == 1


async def test_manager_restart_explicitly_loses_process_handle(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = ProcessProvider(database)
    operation_id = uuid4()
    await ProcessExecutionService(database, provider).start(
        key, request(operation_id)
    )

    restarted_manager = ProcessExecutionService(database, provider)
    with pytest.raises(AgentBoxError) as raised:
        await restarted_manager.inspect(key, operation_id)

    assert raised.value.code == ErrorCode.PROCESS_NOT_RUNNING
    assert raised.value.retry.value == "do_not_retry"


async def test_missing_provider_process_makes_stdin_nonretryable(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
):
    key = await provision_function(database)
    provider = ProcessProvider(database)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()
    await service.start(key, request(operation_id))

    async def missing(*_args, **_kwargs):
        raise ProviderProcessMissing("pid was reused or disappeared")

    monkeypatch.setattr(provider, "send_process_input", missing)
    with pytest.raises(AgentBoxError) as raised:
        await service.send_input(
            key,
            operation_id,
            b"non-idempotent-input",
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    assert raised.value.code == ErrorCode.PROCESS_NOT_RUNNING
    assert raised.value.retry.value == "do_not_retry"


async def test_definitive_rejection_allows_same_operation_retry(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = ProcessProvider(database, reject_once=True)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()

    original = request(operation_id)
    with pytest.raises(AgentBoxError) as raised:
        await service.start(key, original)
    recovered, created = await service.start(key, original)

    assert raised.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert recovered.state == ProcessState.RUNNING
    assert created is True
    assert len(provider.calls) == 2


async def test_explicit_cancellation_fences_late_provider_failure(
    database: StateDatabase,
):
    key = await provision_function(database)
    provider = ProcessProvider(database)
    service = ProcessExecutionService(database, provider)
    operation_id = uuid4()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await service.start(key, request(operation_id))

    terminated = await service.terminate(
        key,
        operation_id,
        grace_seconds=0,
        deadline_at=deadline,
    )
    snapshot = await service.read_output(
        key,
        operation_id,
        after_sequence=0,
        wait_seconds=0,
        deadline_at=deadline,
    )
    inspected = await service.inspect(key, operation_id)

    assert terminated.state == ProcessState.CANCELLED
    assert snapshot.state == ProcessState.CANCELLED
    assert snapshot.exit_code is None
    assert inspected.state == ProcessState.CANCELLED
    assert inspected.exit_code is None

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
    CreatePythonSessionRequest,
    EnvironmentVariable,
    ErrorCode,
    ExecutePythonRequest,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    PythonSessionState,
    RetryDisposition,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderPythonExecutionAmbiguous,
    ProviderPythonExecutionRejected,
    ProviderPythonSessionCreateAmbiguous,
    ProviderPythonSessionCreateRejected,
    ProviderPythonSessionCreateResult,
    ProviderReadyResult,
    ProviderStorageResult,
)
from agentbox.python_sessions import PythonSessionService


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database(tmp_path: Path):
    state = StateDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await state.create_schema_for_test()
    try:
        yield state
    finally:
        await state.dispose()


class Provider:
    name = "fake"
    scope = "fake:test"
    workspace_storage_kind = StorageKind.VOLUME

    def __init__(self, database: StateDatabase) -> None:
        self._database = database
        self.execution_calls = 0
        self.python_create_calls = 0
        self.delete_calls = 0
        self.ambiguous_execution = False
        self.ambiguous_create_once = False
        self.ambiguous_restart_once = False
        self.reject_create_once = False
        self.reject_execution_once = False
        self.reject_delete = False

    def _outside_transaction(self) -> None:
        assert self._database.active_units_of_work == 0

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        self._outside_transaction()
        storage = request.workspace_storage
        return ProviderCreateResult(
            provider_id=f"sandbox-{request.allocation_id}",
            provider_instance_id=f"sandbox-{request.allocation_id}",
            provider_request_id=None,
            workspace_storage=(
                ProviderStorageResult(
                    provider_storage_id=f"volume-{storage.storage_token}",
                    bound_to_allocation=False,
                )
                if storage is not None
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
        self._outside_transaction()
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del allocation, deadline_at
        self._outside_transaction()

    async def create_python_session(
        self,
        allocation: ProviderAllocationRef,
        request: CreatePythonSessionRequest,
    ) -> ProviderPythonSessionCreateResult:
        del allocation
        self._outside_transaction()
        self.python_create_calls += 1
        if self.ambiguous_create_once:
            self.ambiguous_create_once = False
            raise ProviderPythonSessionCreateAmbiguous("create response lost")
        if self.reject_create_once:
            self.reject_create_once = False
            raise ProviderPythonSessionCreateRejected("context service unavailable")
        return ProviderPythonSessionCreateResult(
            provider_context_id=str(request.session_id)
        )

    async def execute_python(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        del allocation, session
        self._outside_transaction()
        self.execution_calls += 1
        if self.ambiguous_execution:
            raise ProviderPythonExecutionAmbiguous("response lost")
        if self.reject_execution_once and self.execution_calls == 1:
            raise ProviderPythonExecutionRejected("not started")
        return PythonResult(
            operation_id=request.operation_id,
            state=PythonExecutionState.SUCCEEDED,
            stdout="ok\n",
            stderr="",
            result="42",
            error_name=None,
            error_message=None,
            traceback=None,
            output_truncated=False,
        )

    async def restart_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> ProviderPythonSessionCreateResult:
        del allocation, deadline_at
        self._outside_transaction()
        if self.ambiguous_restart_once:
            self.ambiguous_restart_once = False
            raise ProviderPythonSessionCreateAmbiguous("restart response lost")
        return ProviderPythonSessionCreateResult(
            provider_context_id=str(session.session_id)
        )

    async def delete_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> None:
        del allocation, session, deadline_at
        self._outside_transaction()
        self.delete_calls += 1
        if self.reject_delete:
            raise RuntimeError("context cleanup failed")

    async def close(self) -> None:
        return None


class BlockingProvider(Provider):
    def __init__(self, database: StateDatabase) -> None:
        super().__init__(database)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute_python(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        del allocation, session
        self.execution_calls += 1
        self.started.set()
        await self.release.wait()
        return PythonResult(
            operation_id=request.operation_id,
            state=PythonExecutionState.SUCCEEDED,
            stdout="",
            stderr="",
            result="42",
            error_name=None,
            error_message=None,
            traceback=None,
            output_truncated=False,
        )


async def provision(
    database: StateDatabase, provider: Provider
) -> tuple[SandboxKey, PythonSessionService, CreatePythonSessionRequest]:
    key = SandboxKey(WorkloadKind.WORKSPACE, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=deadline,
    )
    request = CreatePythonSessionRequest(
        session_id=uuid4(),
        cwd="/workspace",
        environment_keys=("SESSION_TOKEN",),
        deadline_at=deadline,
    )
    service = PythonSessionService(database, provider)
    await service.create(key, request)
    return key, service, request


async def test_python_execution_is_incarnation_local_and_deduplicated(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key, service, session_request = await provision(database, provider)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="40 + 2",
        environment=(EnvironmentVariable("SESSION_TOKEN", "never-persist"),),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )

    first, first_created = await service.execute(
        key, session_request.session_id, request
    )
    second, second_created = await service.execute(
        key, session_request.session_id, request
    )

    assert first.state == PythonExecutionState.SUCCEEDED
    assert second == first
    assert first_created is True
    assert second_created is False
    assert provider.execution_calls == 1
    session = await service.inspect(key, session_request.session_id)
    assert session.environment_keys == ("SESSION_TOKEN",)
    assert "never-persist" not in repr(session)
    assert database.active_units_of_work == 0

    with pytest.raises(AgentBoxError) as changed_secret:
        await service.execute(
            key,
            session_request.session_id,
            replace(
                request,
                environment=(
                    EnvironmentVariable("SESSION_TOKEN", "rotated-secret"),
                ),
            ),
        )
    assert changed_secret.value.code == ErrorCode.OPERATION_CONFLICT


async def test_inspect_marks_session_stale_after_allocation_resume(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key, service, session_request = await provision(database, provider)
    lifecycle = SandboxLifecycleService(database, provider)
    original = await service.inspect(key, session_request.session_id)

    await lifecycle.release(key, deadline_at=session_request.deadline_at)
    resumed = await lifecycle.ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=session_request.deadline_at,
    )
    stale = await service.inspect(key, session_request.session_id)
    replacement, created = await service.create(key, session_request)

    assert resumed.allocation_id == original.allocation_id
    assert resumed.allocation_epoch == original.allocation_epoch + 1
    assert stale.state == PythonSessionState.STALE
    assert created is True
    assert replacement.allocation_epoch == resumed.allocation_epoch


async def test_unknown_execution_is_never_replayed(database: StateDatabase) -> None:
    provider = Provider(database)
    provider.ambiguous_execution = True
    key, service, session_request = await provision(database, provider)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="side_effect()",
        environment=(EnvironmentVariable("SESSION_TOKEN", "never-persist"),),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )

    with pytest.raises(AgentBoxError) as raised:
        await service.execute(key, session_request.session_id, request)
    with pytest.raises(AgentBoxError) as repeated:
        await service.execute(key, session_request.session_id, request)

    assert raised.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert repeated.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert raised.value.retry.value == "do_not_retry"
    assert provider.execution_calls == 1
    assert provider.delete_calls == 1

    # The exact abandoned interpreter was removed, so a later explicit agent
    # retry can recreate the deterministic session without a concurrent fork.
    provider.ambiguous_execution = False
    recreated, created = await service.create(key, session_request)
    assert created is True
    assert recreated.session_id == session_request.session_id
    assert provider.python_create_calls == 2


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_python_create_failure_replaces_unhealthy_allocation(
    database: StateDatabase,
    *,
    ambiguous: bool,
) -> None:
    provider = Provider(database)
    key, service, session_request = await provision(database, provider)
    original = await service.inspect(key, session_request.session_id)
    replacement_request = replace(session_request, session_id=uuid4())
    if ambiguous:
        provider.ambiguous_create_once = True
    else:
        provider.reject_create_once = True

    with pytest.raises(AgentBoxError) as failed:
        await service.create(key, replacement_request)

    assert failed.value.retry == RetryDisposition.DO_NOT_RETRY
    assert failed.value.code == (
        ErrorCode.UNKNOWN_DISPATCH if ambiguous else ErrorCode.ALLOCATION_CHANGED
    )
    async with database.uow() as uow:
        failed_allocation = await uow.repository.current_allocation(key)
        await uow.commit()
    assert failed_allocation is not None
    assert failed_allocation.state.value == "error"

    recovered = await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=session_request.deadline_at,
    )
    recreated, created = await service.create(key, replacement_request)

    assert recovered.allocation_id != original.allocation_id
    assert recreated.allocation_id == recovered.allocation_id
    assert created is True


async def test_failed_unknown_execution_cleanup_is_fenced_until_allocation_changes(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    provider.ambiguous_execution = True
    provider.reject_delete = True
    key, service, session_request = await provision(database, provider)

    with pytest.raises(AgentBoxError) as lost:
        await service.execute(
            key,
            session_request.session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code="side_effect()",
                environment=(),
                output_limit_bytes=1024,
                deadline_at=session_request.deadline_at,
            ),
        )
    assert lost.value.code == ErrorCode.UNKNOWN_DISPATCH
    async with database.uow() as uow:
        failed_allocation = await uow.repository.current_allocation(key)
        await uow.commit()
    assert failed_allocation is not None
    assert failed_allocation.state.value == "error"

    provider.ambiguous_execution = False
    provider.reject_delete = False
    replacement = await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=session_request.deadline_at,
    )
    recreated, created = await service.create(key, session_request)

    assert created is True
    assert recreated.session_id == session_request.session_id
    assert recreated.allocation_id == replacement.allocation_id


async def test_ambiguous_python_restart_replaces_unhealthy_allocation(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key, service, session_request = await provision(database, provider)
    original = await service.inspect(key, session_request.session_id)
    provider.ambiguous_restart_once = True

    with pytest.raises(AgentBoxError) as failed:
        await service.restart(
            key,
            session_request.session_id,
            deadline_at=session_request.deadline_at,
        )

    assert failed.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert failed.value.retry == RetryDisposition.DO_NOT_RETRY
    recovered = await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("workspace-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.INTERACTIVE,
        deadline_at=session_request.deadline_at,
    )
    recreated, created = await service.create(key, session_request)

    assert recovered.allocation_id != original.allocation_id
    assert recreated.allocation_id == recovered.allocation_id
    assert created is True


async def test_conflicting_inflight_python_request_does_not_coalesce(
    database: StateDatabase,
) -> None:
    provider = BlockingProvider(database)
    key, service, session_request = await provision(database, provider)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="slow_side_effect()",
        environment=(),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )
    first = asyncio.create_task(
        service.execute(key, session_request.session_id, request)
    )
    await provider.started.wait()

    with pytest.raises(AgentBoxError) as raised:
        await service.execute(
            key,
            session_request.session_id,
            replace(request, code="different_side_effect()"),
        )

    provider.release.set()
    _ = await first
    assert raised.value.code == ErrorCode.OPERATION_CONFLICT
    assert provider.execution_calls == 1


async def test_cancelled_python_waiter_does_not_leak_inflight_capacity(
    database: StateDatabase,
) -> None:
    provider = BlockingProvider(database)
    key, service, session_request = await provision(database, provider)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="slow_side_effect()",
        environment=(),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )
    pending = asyncio.create_task(
        service.execute(key, session_request.session_id, request)
    )
    await provider.started.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await pending
    provider.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert service._inflight_executions == {}


async def test_live_python_result_is_never_evicted_and_replayed(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key, _old_service, session_request = await provision(database, provider)
    service = PythonSessionService(
        database,
        provider,
        max_execution_results=1,
    )
    await service.create(key, session_request)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="side_effect()",
        environment=(),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )
    first, _ = await service.execute(key, session_request.session_id, request)

    with pytest.raises(AgentBoxError) as full:
        await service.execute(
            key,
            session_request.session_id,
            replace(request, operation_id=uuid4()),
        )
    repeated, created = await service.execute(
        key, session_request.session_id, request
    )

    assert full.value.code == ErrorCode.CAPACITY_EXHAUSTED
    assert repeated == first
    assert created is False
    assert provider.execution_calls == 1


async def test_definitive_execution_rejection_allows_same_operation_retry(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    provider.reject_execution_once = True
    key, service, session_request = await provision(database, provider)
    request = ExecutePythonRequest(
        operation_id=uuid4(),
        code="42",
        environment=(EnvironmentVariable("SESSION_TOKEN", "never-persist"),),
        output_limit_bytes=1024,
        deadline_at=session_request.deadline_at,
    )

    with pytest.raises(AgentBoxError) as raised:
        await service.execute(key, session_request.session_id, request)
    result, created = await service.execute(
        key, session_request.session_id, replace(request)
    )

    assert raised.value.retry.value == "safe_same_operation"
    assert result.state == PythonExecutionState.SUCCEEDED
    assert created is True
    assert provider.execution_calls == 2


async def test_manager_restart_explicitly_loses_python_session(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key, _service, session_request = await provision(database, provider)
    replacement = PythonSessionService(database, provider)

    with pytest.raises(AgentBoxError) as raised:
        await replacement.execute(
            key,
            session_request.session_id,
            ExecutePythonRequest(
                operation_id=uuid4(),
                code="42",
                environment=(),
                output_limit_bytes=1024,
                deadline_at=session_request.deadline_at,
            ),
        )

    assert raised.value.code == ErrorCode.ALLOCATION_CHANGED
    assert raised.value.retry.value == "do_not_retry"

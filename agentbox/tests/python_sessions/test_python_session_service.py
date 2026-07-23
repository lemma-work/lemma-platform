from __future__ import annotations

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
        self.ambiguous_execution = False
        self.reject_execution_once = False

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

    async def create_python_session(
        self,
        allocation: ProviderAllocationRef,
        request: CreatePythonSessionRequest,
    ) -> ProviderPythonSessionCreateResult:
        del allocation
        self._outside_transaction()
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

    async def close(self) -> None:
        return None


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


async def test_python_execution_is_durable_deduplicated_and_outside_uow(
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
    async with database.uow() as uow:
        session = await uow.repository.get_python_session(
            key, session_request.session_id
        )
        await uow.commit()
    assert session is not None
    assert session.environment_keys == ("SESSION_TOKEN",)
    assert "never-persist" not in repr(session)
    assert database.active_units_of_work == 0


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
    existing, created = await service.execute(key, session_request.session_id, request)

    assert raised.value.code == ErrorCode.UNKNOWN_DISPATCH
    assert existing.state == PythonExecutionState.UNKNOWN
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
    assert created is False
    assert provider.execution_calls == 2

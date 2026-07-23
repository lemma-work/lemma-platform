from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    ByteRange,
    ErrorCode,
    FileKind,
    FileStat,
    RetryDisposition,
    SandboxKey,
    SandboxProfileRef,
    StorageKind,
    WorkloadKind,
)
from agentbox.filesystem import FilesystemService
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.ports import (
    ProviderAllocationRef,
    ProviderCreateRequest,
    ProviderCreateResult,
    ProviderFilesystemConflict,
    ProviderFilesystemNotFound,
    ProviderFilesystemRejected,
    ProviderFilesystemUnavailable,
    ProviderReadyResult,
)


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
        self.data = b"binary-data"
        self.calls: list[str] = []

    def _outside_transaction(self, name: str) -> None:
        assert self._database.active_units_of_work == 0
        self.calls.append(name)

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult:
        self._outside_transaction("create")
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
        self._outside_transaction("ready")
        return ProviderReadyResult(
            provider_id=allocation.provider_id,
            provider_instance_id=allocation.provider_instance_id,
        )

    async def stat_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> FileStat:
        del allocation, deadline_at
        self._outside_transaction("stat")
        return self._stat(path)

    async def list_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> tuple[FileStat, ...]:
        del allocation, deadline_at
        self._outside_transaction("list")
        return (self._stat(f"{path}/payload"),)

    async def read_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> bytes:
        del allocation, path, deadline_at
        self._outside_transaction("read")
        end = (
            None if byte_range.length is None else byte_range.offset + byte_range.length
        )
        return self.data[byte_range.offset : end]

    async def write_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        data: bytes,
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        del allocation, expected_sha256, deadline_at
        self._outside_transaction("write")
        self.data = data
        return self._stat(path)

    async def move_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        del allocation, source, destination, deadline_at
        self._outside_transaction("move")

    async def delete_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        del allocation, path, recursive, deadline_at
        self._outside_transaction("delete")
        return True

    async def close(self) -> None:
        return None

    def _stat(self, path: str) -> FileStat:
        return FileStat(
            path=path,
            kind=FileKind.FILE,
            size_bytes=len(self.data),
            modified_at=datetime.now(timezone.utc),
            mode=0o600,
        )


async def test_all_filesystem_provider_io_occurs_after_uow_closes(
    database: StateDatabase,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("function-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=deadline,
    )
    service = FilesystemService(database, provider)

    assert (await service.stat(key, "/tmp/payload", deadline_at=deadline)).path
    assert await service.list(key, "/tmp", deadline_at=deadline)
    assert (
        await service.read(key, "/tmp/payload", ByteRange(1, 4), deadline_at=deadline)
        == b"inar"
    )
    await service.write(
        key,
        "/tmp/payload",
        b"replacement",
        expected_sha256=None,
        deadline_at=deadline,
    )
    await service.move(key, "/tmp/payload", "/tmp/moved", deadline_at=deadline)
    assert await service.delete(
        key, "/tmp/moved", recursive=False, deadline_at=deadline
    )

    assert provider.calls == [
        "create",
        "ready",
        "stat",
        "list",
        "read",
        "write",
        "move",
        "delete",
    ]
    assert database.active_units_of_work == 0


@pytest.mark.parametrize(
    ("provider_error", "code", "status_code", "retry", "retry_after_ms"),
    [
        (
            ProviderFilesystemNotFound("missing"),
            ErrorCode.FILE_NOT_FOUND,
            404,
            RetryDisposition.DO_NOT_RETRY,
            None,
        ),
        (
            ProviderFilesystemConflict("digest mismatch"),
            ErrorCode.FILE_CONFLICT,
            409,
            RetryDisposition.DO_NOT_RETRY,
            None,
        ),
        (
            ProviderFilesystemRejected("too large", status_code=413),
            ErrorCode.INVALID_REQUEST,
            413,
            RetryDisposition.DO_NOT_RETRY,
            None,
        ),
        (
            ProviderFilesystemUnavailable("rate limited", retry_after_ms=750),
            ErrorCode.PROVIDER_UNAVAILABLE,
            503,
            RetryDisposition.WAIT,
            750,
        ),
    ],
)
async def test_filesystem_provider_failures_have_typed_public_semantics(
    database: StateDatabase,
    monkeypatch: pytest.MonkeyPatch,
    provider_error: RuntimeError,
    code: ErrorCode,
    status_code: int,
    retry: RetryDisposition,
    retry_after_ms: int | None,
) -> None:
    provider = Provider(database)
    key = SandboxKey(WorkloadKind.FUNCTION, uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await SandboxLifecycleService(database, provider).ensure(
        key,
        SandboxProfileRef("function-python-v1", f"sha256:{'a' * 64}"),
        admission_class=AdmissionClass.LATENCY,
        deadline_at=deadline,
    )

    async def fail_stat(*_args, **_kwargs):
        raise provider_error

    monkeypatch.setattr(provider, "stat_file", fail_stat)

    with pytest.raises(AgentBoxError) as raised:
        await FilesystemService(database, provider).stat(
            key, "/workspace/missing", deadline_at=deadline
        )

    assert raised.value.code == code
    assert raised.value.status_code == status_code
    assert raised.value.retry == retry
    assert raised.value.retry_after_ms == retry_after_ms
    assert database.active_units_of_work == 0

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    PythonResult,
    PythonSessionRef,
    ProcessRef,
    ProcessOutputSnapshot,
    SandboxKey,
    SandboxProfileRef,
    StartProcessRequest,
    StorageKind,
    TerminalSize,
    PortProtocol,
)


@dataclass(frozen=True, slots=True)
class ProviderMetadataEntry:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class ProviderCreateRequest:
    allocation_id: UUID
    allocation_token: UUID
    key: SandboxKey
    profile: SandboxProfileRef
    deadline_at: datetime
    metadata: tuple[ProviderMetadataEntry, ...]
    workspace_storage: ProviderStorageRequest | None


@dataclass(frozen=True, slots=True)
class ProviderStorageRequest:
    storage_kind: StorageKind
    storage_token: UUID
    provider_storage_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderStorageResult:
    provider_storage_id: str
    bound_to_allocation: bool


@dataclass(frozen=True, slots=True)
class ProviderCreateResult:
    provider_id: str
    provider_instance_id: str | None
    provider_request_id: str | None
    workspace_storage: ProviderStorageResult | None


@dataclass(frozen=True, slots=True)
class ProviderAllocationRef:
    provider_id: str
    provider_instance_id: str | None
    allocation_id: UUID
    allocation_token: UUID
    key: SandboxKey
    resource_generation: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderReadyResult:
    provider_id: str
    provider_instance_id: str | None


@dataclass(frozen=True, slots=True)
class ProviderInventoryAllocation:
    provider_id: str
    provider_instance_id: str | None
    workspace_storage: ProviderStorageResult | None = None


class ProviderCreateAmbiguous(RuntimeError):
    """The provider may have accepted create, so it must not be repeated."""


class ProviderCreateRejected(RuntimeError):
    """The provider definitively rejected create before allocating compute."""


class ProviderRateLimited(RuntimeError):
    """The provider rejected admission without allocating compute."""

    def __init__(self, message: str, *, retry_after_ms: int) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class ProviderNotReady(RuntimeError):
    """The exact provider allocation exists but is not ready by this deadline."""

    def __init__(self, message: str, *, retry_after_ms: int) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class ProviderAllocationFailed(RuntimeError):
    """The exact provider allocation exists but cannot become ready."""


class SandboxProviderPort(Protocol):
    name: str
    scope: str
    workspace_storage_kind: StorageKind

    async def create(self, request: ProviderCreateRequest) -> ProviderCreateResult: ...

    async def wait_ready(
        self,
        allocation: ProviderAllocationRef,
        *,
        profile: SandboxProfileRef,
        deadline_at: datetime,
    ) -> ProviderReadyResult: ...

    async def release_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None: ...

    async def destroy_allocation(
        self,
        allocation: ProviderAllocationRef,
        *,
        deadline_at: datetime,
    ) -> None: ...

    async def destroy_workspace_storage(
        self,
        provider_storage_id: str,
        *,
        deadline_at: datetime,
    ) -> None: ...

    async def find_allocations(
        self,
        metadata: tuple[ProviderMetadataEntry, ...],
        *,
        deadline_at: datetime,
    ) -> tuple[ProviderInventoryAllocation, ...]: ...

    async def close(self) -> None: ...


class ProviderLifecycleError(RuntimeError):
    """An exact release/destroy operation failed and is safe to reconcile."""


@dataclass(frozen=True, slots=True)
class ProviderProcessStartRequest:
    allocation: ProviderAllocationRef
    process: ProcessRef
    request: StartProcessRequest


@dataclass(frozen=True, slots=True)
class ProviderProcessStartResult:
    provider_process_id: str
    provider_tag: str


class ProviderProcessStartAmbiguous(RuntimeError):
    """Process start may have been accepted and must never be replayed."""


class ProviderProcessStartRejected(RuntimeError):
    """Process start definitively failed before a process was created."""


class ProviderProcessMissing(RuntimeError):
    """The exact provider process no longer accepts process operations."""


class ProviderFilesystemNotFound(RuntimeError):
    """The requested path definitively does not exist in the allocation."""


class ProviderFilesystemConflict(RuntimeError):
    """A filesystem precondition or destination constraint was not satisfied."""


class ProviderFilesystemRejected(RuntimeError):
    """The provider definitively rejected the filesystem operation."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderFilesystemUnavailable(RuntimeError):
    """Filesystem outcome is unavailable and must not be inferred as not-found."""

    def __init__(self, message: str, *, retry_after_ms: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class ProviderAllocationMissing(ProviderFilesystemUnavailable):
    """The allocation backing an operation definitively no longer exists."""


class ProviderProcessPort(Protocol):
    async def start_process(
        self, request: ProviderProcessStartRequest
    ) -> ProviderProcessStartResult: ...

    async def send_process_input(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        data: bytes,
        deadline_at: datetime,
    ) -> None: ...

    async def read_process_output(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot: ...

    async def resize_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None: ...

    async def terminate_process(
        self,
        allocation: ProviderAllocationRef,
        *,
        process: ProcessRef,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None: ...


class ProviderFilesystemPort(Protocol):
    async def create_directory(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> None: ...

    async def stat_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> FileStat: ...

    async def list_files(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        deadline_at: datetime,
    ) -> tuple[FileStat, ...]: ...

    async def open_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]: ...

    async def write_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat: ...

    async def move_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None: ...

    async def delete_file(
        self,
        allocation: ProviderAllocationRef,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProviderPythonSessionCreateResult:
    provider_context_id: str


class ProviderPythonSessionCreateAmbiguous(RuntimeError):
    """Session creation may have succeeded and the handle must be invalidated."""


class ProviderPythonSessionCreateRejected(RuntimeError):
    """Session creation definitively failed before a context was created."""


class ProviderPythonExecutionAmbiguous(RuntimeError):
    """Python execution may have run and must not be replayed automatically."""


class ProviderPythonExecutionRejected(RuntimeError):
    """Python execution definitively failed before user code began."""


class ProviderPythonSessionPort(Protocol):
    async def create_python_session(
        self,
        allocation: ProviderAllocationRef,
        request: CreatePythonSessionRequest,
    ) -> ProviderPythonSessionCreateResult: ...

    async def execute_python(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult: ...

    async def restart_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> ProviderPythonSessionCreateResult: ...

    async def delete_python_session(
        self,
        allocation: ProviderAllocationRef,
        session: PythonSessionRef,
        *,
        deadline_at: datetime,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderPortTarget:
    base_url: str
    headers: tuple[ProviderMetadataEntry, ...] = ()


class ProviderPortAccessPort(Protocol):
    async def resolve_port_target(
        self,
        allocation: ProviderAllocationRef,
        *,
        port: int,
        protocol: PortProtocol,
        deadline_at: datetime,
        activity_until: datetime | None = None,
    ) -> ProviderPortTarget: ...

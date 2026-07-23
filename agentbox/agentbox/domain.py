from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import UUID


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkloadKind(StrEnum):
    WORKSPACE = "workspace"
    FUNCTION = "function"


class AdmissionClass(StrEnum):
    INTERACTIVE = "interactive"
    LATENCY = "latency"
    BATCH = "batch"


class AdmissionState(StrEnum):
    UNRESERVED = "unreserved"
    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASED = "released"


class SandboxDesiredState(StrEnum):
    PRESENT = "present"
    RELEASED = "released"
    DELETED = "deleted"


class MaintenanceAction(StrEnum):
    RELEASE = "release"
    DESTROY = "destroy"


class AllocationState(StrEnum):
    RESERVED = "reserved"
    PROVISIONING = "provisioning"
    UNKNOWN = "unknown"
    ACTIVE = "active"
    QUIESCING = "quiescing"
    RELEASED = "released"
    DRAINING = "draining"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    ERROR = "error"


class DispatchState(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"
    RESOLVED = "resolved"


class ProcessState(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    UNKNOWN = "unknown"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class PythonSessionState(StrEnum):
    RESERVED = "reserved"
    CREATING = "creating"
    UNKNOWN = "unknown"
    ACTIVE = "active"
    STALE = "stale"
    DELETED = "deleted"


class PythonExecutionState(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ProcessOutputChannel(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    PTY = "pty"


class FileKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class StorageState(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    MIGRATING = "migrating"
    DELETING = "deleting"
    DELETED = "deleted"
    ERROR = "error"


class StorageKind(StrEnum):
    VOLUME = "volume"
    PVC = "pvc"
    SANDBOX_NATIVE = "sandbox_native"


class RetryDisposition(StrEnum):
    WAIT = "wait"
    SAFE_SAME_OPERATION = "safe_same_operation"
    DO_NOT_RETRY = "do_not_retry"


class ErrorCode(StrEnum):
    CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    PROVISIONING = "PROVISIONING"
    AMBIGUOUS_CREATE = "AMBIGUOUS_CREATE"
    UNKNOWN_DISPATCH = "UNKNOWN_DISPATCH"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    OPERATION_CONFLICT = "OPERATION_CONFLICT"
    ALLOCATION_CHANGED = "ALLOCATION_CHANGED"
    SANDBOX_QUIESCING = "SANDBOX_QUIESCING"
    SANDBOX_NOT_FOUND = "SANDBOX_NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_CONFLICT = "FILE_CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"
    INTERNAL = "INTERNAL"


class SandboxCapability(StrEnum):
    PROCESS = "process"
    PTY = "pty"
    PYTHON_SESSION = "python_session"
    FILESYSTEM = "filesystem"
    PORT_ACCESS = "port_access"
    BROWSER = "browser"


class PortProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"


@dataclass(frozen=True, slots=True)
class CapacityErrorContext:
    kind: Literal["capacity"]
    provider_scope: str
    active: int
    reserved: int
    limit: int


@dataclass(frozen=True, slots=True)
class OperationConflictContext:
    kind: Literal["operation_conflict"]
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class ProviderErrorContext:
    kind: Literal["provider"]
    provider_name: str
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class AllocationErrorContext:
    kind: Literal["allocation"]
    allocation_id: UUID | None
    allocation_epoch: int | None


@dataclass(frozen=True, slots=True)
class ProcessErrorContext:
    kind: Literal["process"]
    operation_id: UUID


ErrorContext = (
    CapacityErrorContext
    | OperationConflictContext
    | ProviderErrorContext
    | AllocationErrorContext
    | ProcessErrorContext
)


@dataclass(frozen=True, slots=True)
class SandboxKey:
    workload_kind: WorkloadKind
    logical_id: UUID


@dataclass(frozen=True, slots=True)
class SandboxProfileRef:
    name: str
    digest: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 128:
            raise ValueError("profile name must contain 1-128 characters")
        if not self.digest.startswith("sha256:") or len(self.digest) != 71:
            raise ValueError("profile digest must be sha256:<64 hex characters>")
        try:
            int(self.digest[7:], 16)
        except ValueError as exc:
            raise ValueError(
                "profile digest must contain hexadecimal characters"
            ) from exc


@dataclass(frozen=True, slots=True)
class ProviderAdmissionPolicy:
    max_active: int
    create_rate_per_second: float
    create_burst: int
    interactive_capacity_reserve: int = 0
    latency_capacity_reserve: int = 0

    def __post_init__(self) -> None:
        if self.max_active < 1:
            raise ValueError("provider max_active must be positive")
        if self.create_rate_per_second <= 0 or self.create_burst < 1:
            raise ValueError("provider create rate and burst must be positive")
        if min(self.interactive_capacity_reserve, self.latency_capacity_reserve) < 0:
            raise ValueError("provider capacity reserves cannot be negative")
        if (
            self.interactive_capacity_reserve + self.latency_capacity_reserve
            >= self.max_active
        ):
            raise ValueError("provider capacity reserves must leave batch capacity")

    @classmethod
    def permissive_for_tests(cls) -> ProviderAdmissionPolicy:
        return cls(
            max_active=10_000,
            create_rate_per_second=10_000,
            create_burst=10_000,
        )


@dataclass(frozen=True, slots=True)
class ProviderAdmissionDecision:
    accepted: bool
    active: int
    reserved: int
    limit: int
    error_code: ErrorCode | None = None
    retry_after_ms: int | None = None


@dataclass(frozen=True, slots=True)
class LogicalSandbox:
    key: SandboxKey
    desired_state: SandboxDesiredState
    profile: SandboxProfileRef
    current_allocation_id: UUID | None
    allocation_epoch: int
    last_used_at: datetime
    released_at: datetime | None
    delete_after: datetime | None


@dataclass(frozen=True, slots=True)
class SandboxMaintenanceClaim:
    key: SandboxKey
    action: MaintenanceAction
    token: UUID
    claimed_until: datetime


@dataclass(frozen=True, slots=True)
class PhysicalAllocation:
    allocation_id: UUID
    key: SandboxKey
    allocation_token: UUID
    provider_name: str
    provider_scope: str
    provider_id: str | None
    provider_instance_id: str | None
    profile_name: str
    profile_digest: str
    state: AllocationState
    allocation_epoch: int | None
    retry_after: datetime | None

    @property
    def profile(self) -> SandboxProfileRef:
        return SandboxProfileRef(name=self.profile_name, digest=self.profile_digest)


@dataclass(frozen=True, slots=True)
class CreateReconcileCandidate:
    allocation: PhysicalAllocation
    dispatch_state: DispatchState
    dispatch_started_at: datetime
    reconcile_after: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceStorage:
    key: SandboxKey
    provider_name: str
    storage_kind: StorageKind
    provider_storage_id: str | None
    bound_allocation_id: UUID | None
    state: StorageState
    content_generation: int
    storage_token: UUID


@dataclass(frozen=True, slots=True)
class AllocationIntent:
    logical: LogicalSandbox
    allocation: PhysicalAllocation
    dispatch_state: DispatchState
    should_dispatch_create: bool


@dataclass(frozen=True, slots=True)
class ProcessIntent:
    key: SandboxKey
    operation_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    request_hash: str
    state: ProcessState
    provider_process_id: str | None
    provider_tag: str | None
    cwd: str
    tty: bool
    output_limit_bytes: int
    deadline_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class EnvironmentVariable:
    name: str
    value: str

    def __post_init__(self) -> None:
        if not self.name or "=" in self.name or "\x00" in self.name:
            raise ValueError("environment variable name is invalid")
        if "\x00" in self.value:
            raise ValueError("environment variable value cannot contain NUL")


@dataclass(frozen=True, slots=True)
class CreatePythonSessionRequest:
    session_id: UUID
    cwd: str
    environment_keys: tuple[str, ...]
    deadline_at: datetime

    def __post_init__(self) -> None:
        if not self.cwd.startswith("/"):
            raise ValueError("Python session cwd must be absolute")
        if any(
            not name or "=" in name or "\x00" in name for name in self.environment_keys
        ):
            raise ValueError("environment variable name is invalid")
        if len(self.environment_keys) != len(set(self.environment_keys)):
            raise ValueError("environment variable names must be unique")


@dataclass(frozen=True, slots=True)
class PythonSessionRef:
    key: SandboxKey
    session_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    provider_context_id: str | None
    cwd: str
    environment_keys: tuple[str, ...]
    state: PythonSessionState


@dataclass(frozen=True, slots=True)
class ExecutePythonRequest:
    operation_id: UUID
    code: str
    environment: tuple[EnvironmentVariable, ...]
    output_limit_bytes: int
    deadline_at: datetime

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("Python code cannot be empty")
        if not 1 <= self.output_limit_bytes <= 64 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be in 1..67108864")
        names = tuple(item.name for item in self.environment)
        if len(names) != len(set(names)):
            raise ValueError("environment variable names must be unique")


@dataclass(frozen=True, slots=True)
class PythonResult:
    operation_id: UUID
    state: PythonExecutionState
    stdout: str
    stderr: str
    result: str | None
    error_name: str | None
    error_message: str | None
    traceback: str | None
    output_truncated: bool


@dataclass(frozen=True, slots=True)
class TerminalSize:
    cols: int
    rows: int

    def __post_init__(self) -> None:
        if not 1 <= self.cols <= 1000 or not 1 <= self.rows <= 1000:
            raise ValueError("terminal dimensions must be in 1..1000")


@dataclass(frozen=True, slots=True)
class StartProcessRequest:
    operation_id: UUID
    shell_command: str | None
    argv: tuple[str, ...] | None
    cwd: str
    environment: tuple[EnvironmentVariable, ...]
    tty: TerminalSize | None
    output_limit_bytes: int
    deadline_at: datetime

    def __post_init__(self) -> None:
        if (self.shell_command is None) == (self.argv is None):
            raise ValueError("exactly one of shell_command and argv is required")
        if self.shell_command is not None and not self.shell_command:
            raise ValueError("shell_command cannot be empty")
        if self.argv is not None and (
            not self.argv or any(not arg for arg in self.argv)
        ):
            raise ValueError("argv must contain non-empty arguments")
        if not self.cwd.startswith("/"):
            raise ValueError("process cwd must be absolute")
        if not 1 <= self.output_limit_bytes <= 64 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be in 1..67108864")
        names = tuple(item.name for item in self.environment)
        if len(names) != len(set(names)):
            raise ValueError("environment variable names must be unique")


@dataclass(frozen=True, slots=True)
class ProcessRef:
    key: SandboxKey
    operation_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    provider_process_id: str | None
    state: ProcessState
    cwd: str
    tty: bool
    output_limit_bytes: int
    deadline_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class ProcessOutputChunk:
    sequence: int
    channel: ProcessOutputChannel
    data: bytes


@dataclass(frozen=True, slots=True)
class ProcessOutputSnapshot:
    chunks: tuple[ProcessOutputChunk, ...]
    next_sequence: int
    truncated_before_sequence: int | None
    state: ProcessState
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class FileStat:
    path: str
    kind: FileKind
    size_bytes: int
    modified_at: datetime
    mode: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ByteRange:
    offset: int
    length: int | None

    def __post_init__(self) -> None:
        if self.offset < 0 or self.length is not None and self.length < 0:
            raise ValueError("file byte range cannot be negative")


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    key: SandboxKey
    desired_state: SandboxDesiredState
    profile: SandboxProfileRef
    allocation_state: AllocationState | None
    allocation_id: UUID | None
    allocation_epoch: int
    ready: bool
    operation_id: UUID | None
    retry_after_ms: int | None


@dataclass(frozen=True, slots=True)
class PortAccessGrant:
    key: SandboxKey
    port: int
    protocol: PortProtocol
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PortAccessClaims:
    key: SandboxKey
    allocation_id: UUID
    allocation_epoch: int
    port: int
    protocol: PortProtocol
    expires_at: datetime


class AgentBoxError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retry: RetryDisposition,
        status_code: int,
        retry_after_ms: int | None = None,
        context: ErrorContext | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry = retry
        self.status_code = status_code
        self.retry_after_ms = retry_after_ms
        self.context = context

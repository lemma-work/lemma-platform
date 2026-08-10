"""The vocabulary the backend and a sandbox runtime both speak.

This is the protocol, not a domain model: value types that cross the boundary
between the workspace module and the HTTP server running inside a sandbox
image. Both sides import it, which is why it lives beside the runtime rather
than inside `app/` -- a sandbox image must not need the backend to start.

It is deliberately smaller than what it replaced. The sandbox manager kept its
allocation, admission and reconciliation state machine in the same file; none
of that survived the move to deterministic naming, so none of it survived here.
"""

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




class SandboxDesiredState(StrEnum):
    PRESENT = "present"
    RELEASED = "released"
    DELETED = "deleted"




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
    PROCESS_NOT_RUNNING = "PROCESS_NOT_RUNNING"
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
    """Which sandbox an operation belongs to: its kind and its owner."""

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
        if not 1 <= self.output_limit_bytes <= 2 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be in 1..2097152")
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
    initial_input: bytes | None = None

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
        if not 1 <= self.output_limit_bytes <= 2 * 1024 * 1024:
            raise ValueError("output_limit_bytes must be in 1..2097152")
        if self.initial_input is not None and len(self.initial_input) > 1024 * 1024:
            raise ValueError("initial process input exceeds 1048576 bytes")
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
    # Increments whenever this workspace's durable disk is recreated, which is
    # the only way a caller can distinguish "your files are gone" from a
    # perfectly ordinary empty directory. None for workloads without storage.
    storage_generation: int | None = None


@dataclass(frozen=True, slots=True)
class PortAccessGrant:
    key: SandboxKey
    port: int
    protocol: PortProtocol
    url: str
    expires_at: datetime




class SandboxRuntimeError(RuntimeError):
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


@dataclass(frozen=True, slots=True)
class RuntimeRequestHeader:
    """One header a caller must send to reach a function runtime."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class FunctionRuntimeLease:
    """Where a pod's function runtime is, and for how long that stays true.

    The epoch is the fence: a lease naming an old one belongs to a sandbox that
    has since been replaced, and using it would talk to the wrong container.
    """

    logical_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    profile: SandboxProfileRef
    url: str
    request_headers: tuple[RuntimeRequestHeader, ...]
    expires_at: datetime

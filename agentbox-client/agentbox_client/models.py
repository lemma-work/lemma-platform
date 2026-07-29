from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkloadKind(StrEnum):
    WORKSPACE = "workspace"
    FUNCTION = "function"


class AdmissionClass(StrEnum):
    INTERACTIVE = "interactive"
    LATENCY = "latency"
    BATCH = "batch"


class RetryDisposition(StrEnum):
    WAIT = "wait"
    SAFE_SAME_OPERATION = "safe_same_operation"
    DO_NOT_RETRY = "do_not_retry"


class ProfileRef(StrictModel):
    name: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SandboxHandle(StrictModel):
    workload_kind: WorkloadKind
    logical_id: UUID
    desired_state: str
    profile: ProfileRef
    allocation_state: str | None
    allocation_id: UUID | None
    allocation_epoch: int
    ready: bool
    operation_id: UUID | None
    retry_after_ms: int | None


class EnvironmentVariable(StrictModel):
    name: str
    value: str


class TerminalSize(StrictModel):
    cols: int = Field(ge=1, le=1000)
    rows: int = Field(ge=1, le=1000)


class PortProtocol(StrEnum):
    HTTP = "http"
    HTTPS = "https"


class PortAccessGrant(StrictModel):
    workload_kind: WorkloadKind
    logical_id: UUID
    port: int
    protocol: PortProtocol
    url: str
    expires_at: datetime


class RuntimeRequestHeader(StrictModel):
    name: str
    value: str = Field(repr=False)


class FunctionRuntimeLease(StrictModel):
    logical_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    profile: ProfileRef
    url: str
    request_headers: tuple[RuntimeRequestHeader, ...] = Field(repr=False)
    expires_at: datetime


class ProcessState(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    UNKNOWN = "unknown"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ProcessRef(StrictModel):
    operation_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    state: ProcessState
    cwd: str
    tty: bool
    output_limit_bytes: int
    deadline_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    exit_code: int | None


class ProcessOutputChannel(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    PTY = "pty"


class ProcessOutputChunk(StrictModel):
    sequence: int
    channel: ProcessOutputChannel
    data: bytes


class ProcessOutputSnapshot(StrictModel):
    chunks: tuple[ProcessOutputChunk, ...]
    next_sequence: int
    truncated_before_sequence: int | None
    state: ProcessState
    exit_code: int | None


class PythonSessionState(StrEnum):
    RESERVED = "reserved"
    CREATING = "creating"
    UNKNOWN = "unknown"
    ACTIVE = "active"
    STALE = "stale"
    DELETED = "deleted"


class PythonSession(StrictModel):
    session_id: UUID
    allocation_id: UUID
    allocation_epoch: int
    cwd: str
    environment_keys: tuple[str, ...]
    state: PythonSessionState


class PythonExecutionState(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class PythonResult(StrictModel):
    operation_id: UUID
    state: PythonExecutionState
    stdout: str
    stderr: str
    result: str | None
    error_name: str | None
    error_message: str | None
    traceback: str | None
    output_truncated: bool


class FileKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class FileStat(StrictModel):
    path: str
    kind: FileKind
    size_bytes: int
    modified_at: datetime
    mode: int
    sha256: str | None


class FileList(StrictModel):
    entries: tuple[FileStat, ...]


class CapacityErrorContext(StrictModel):
    kind: Literal["capacity"]
    provider_scope: str
    active: int
    reserved: int
    limit: int


class OperationConflictContext(StrictModel):
    kind: Literal["operation_conflict"]
    operation_id: UUID


class ProviderErrorContext(StrictModel):
    kind: Literal["provider"]
    provider_name: str
    provider_request_id: str | None = None


class AllocationErrorContext(StrictModel):
    kind: Literal["allocation"]
    allocation_id: UUID | None
    allocation_epoch: int | None


class ProcessErrorContext(StrictModel):
    kind: Literal["process"]
    operation_id: UUID


ErrorContext = Annotated[
    CapacityErrorContext
    | OperationConflictContext
    | ProviderErrorContext
    | AllocationErrorContext
    | ProcessErrorContext,
    Field(discriminator="kind"),
]


class AgentBoxErrorBody(StrictModel):
    code: str
    message: str
    retry: RetryDisposition
    retry_after_ms: int | None = None
    context: ErrorContext | None = None


class AgentBoxErrorResponse(StrictModel):
    error: AgentBoxErrorBody

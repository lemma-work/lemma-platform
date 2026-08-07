from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbox.domain import (
    AdmissionClass,
    AgentBoxError,
    AllocationErrorContext,
    AllocationState,
    CapacityErrorContext,
    ErrorCode,
    EnvironmentVariable,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    OperationConflictContext,
    ProcessErrorContext,
    ProcessRef,
    ProcessState,
    PythonExecutionState,
    PythonResult,
    PythonSessionRef,
    PythonSessionState,
    ProviderErrorContext,
    RetryDisposition,
    SandboxDesiredState,
    SandboxHandle,
    SandboxProfileRef,
    StartProcessRequest,
    TerminalSize,
    WorkloadKind,
    PortAccessGrant,
    PortProtocol,
)


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProfileRefModel(StrictApiModel):
    name: str = Field(min_length=1, max_length=128)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    def to_domain(self) -> SandboxProfileRef:
        return SandboxProfileRef(name=self.name, digest=self.digest)

    @classmethod
    def from_domain(cls, profile: SandboxProfileRef) -> ProfileRefModel:
        return cls(name=profile.name, digest=profile.digest)


class EnsureSandboxRequest(StrictApiModel):
    profile: ProfileRefModel
    admission_class: AdmissionClass
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value


class SandboxHandleResponse(StrictApiModel):
    workload_kind: WorkloadKind
    logical_id: UUID
    desired_state: SandboxDesiredState
    profile: ProfileRefModel
    allocation_state: AllocationState | None
    allocation_id: UUID | None
    allocation_epoch: int = Field(ge=0)
    ready: bool
    operation_id: UUID | None
    retry_after_ms: int | None = Field(default=None, ge=0)
    # Increments whenever the workspace's durable disk is recreated. Lets a
    # caller distinguish "your files are gone" from an ordinary empty
    # directory. Absent for workloads without durable storage.
    storage_generation: int | None = Field(default=None, ge=0)

    @classmethod
    def from_domain(cls, handle: SandboxHandle) -> SandboxHandleResponse:
        return cls(
            workload_kind=handle.key.workload_kind,
            logical_id=handle.key.logical_id,
            desired_state=handle.desired_state,
            profile=ProfileRefModel.from_domain(handle.profile),
            allocation_state=handle.allocation_state,
            allocation_id=handle.allocation_id,
            allocation_epoch=handle.allocation_epoch,
            ready=handle.ready,
            operation_id=handle.operation_id,
            retry_after_ms=handle.retry_after_ms,
            storage_generation=handle.storage_generation,
        )


class EnvironmentVariableModel(StrictApiModel):
    name: str = Field(min_length=1, max_length=256)
    value: str = Field(max_length=65536)

    def to_domain(self) -> EnvironmentVariable:
        return EnvironmentVariable(name=self.name, value=self.value)


class TerminalSizeModel(StrictApiModel):
    cols: int = Field(ge=1, le=1000)
    rows: int = Field(ge=1, le=1000)

    def to_domain(self) -> TerminalSize:
        return TerminalSize(cols=self.cols, rows=self.rows)


class StartProcessModel(StrictApiModel):
    operation_id: UUID
    shell_command: str | None = Field(default=None, min_length=1, max_length=1048576)
    argv: tuple[str, ...] | None = None
    cwd: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    environment: tuple[EnvironmentVariableModel, ...] = ()
    tty: TerminalSizeModel | None = None
    output_limit_bytes: int = Field(default=1048576, ge=1, le=2097152)
    deadline_at: datetime
    initial_input_base64: str | None = Field(
        default=None,
        max_length=1_398_104,
    )

    @model_validator(mode="after")
    def validate_command_and_deadline(self) -> StartProcessModel:
        if (self.shell_command is None) == (self.argv is None):
            raise ValueError("exactly one of shell_command and argv is required")
        if self.argv is not None and (
            not self.argv or any(not arg for arg in self.argv)
        ):
            raise ValueError("argv must contain non-empty arguments")
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        names = tuple(item.name for item in self.environment)
        if len(names) != len(set(names)):
            raise ValueError("environment variable names must be unique")
        if self.initial_input_base64 is not None:
            try:
                initial_input = base64.b64decode(
                    self.initial_input_base64, validate=True
                )
            except ValueError as exc:
                raise ValueError("initial_input_base64 must be valid base64") from exc
            if len(initial_input) > 1024 * 1024:
                raise ValueError("initial process input exceeds 1048576 bytes")
        return self

    def to_domain(self) -> StartProcessRequest:
        return StartProcessRequest(
            operation_id=self.operation_id,
            shell_command=self.shell_command,
            argv=self.argv,
            cwd=self.cwd,
            environment=tuple(item.to_domain() for item in self.environment),
            tty=self.tty.to_domain() if self.tty is not None else None,
            output_limit_bytes=self.output_limit_bytes,
            deadline_at=self.deadline_at,
            initial_input=(
                base64.b64decode(self.initial_input_base64, validate=True)
                if self.initial_input_base64 is not None
                else None
            ),
        )


class ProcessRefResponse(StrictApiModel):
    operation_id: UUID
    allocation_id: UUID
    allocation_epoch: int = Field(ge=1)
    state: ProcessState
    cwd: str
    tty: bool
    output_limit_bytes: int = Field(ge=1, le=2 * 1024 * 1024)
    deadline_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    exit_code: int | None

    @classmethod
    def from_domain(cls, process: ProcessRef) -> ProcessRefResponse:
        return cls(
            operation_id=process.operation_id,
            allocation_id=process.allocation_id,
            allocation_epoch=process.allocation_epoch,
            state=process.state,
            cwd=process.cwd,
            tty=process.tty,
            output_limit_bytes=process.output_limit_bytes,
            deadline_at=process.deadline_at,
            started_at=process.started_at,
            completed_at=process.completed_at,
            exit_code=process.exit_code,
        )


class ResizeProcessRequest(StrictApiModel):
    size: TerminalSizeModel
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value


class FileStatResponse(StrictApiModel):
    path: str
    kind: FileKind
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    mode: int = Field(ge=0)
    sha256: str | None = None

    @classmethod
    def from_domain(cls, stat: FileStat) -> FileStatResponse:
        return cls(
            path=stat.path,
            kind=stat.kind,
            size_bytes=stat.size_bytes,
            modified_at=stat.modified_at,
            mode=stat.mode,
            sha256=stat.sha256,
        )


class FileListResponse(StrictApiModel):
    entries: tuple[FileStatResponse, ...]


class MoveFileRequest(StrictApiModel):
    source: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    destination: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value


class CreatePythonSessionModel(StrictApiModel):
    cwd: str = Field(default="/workspace", min_length=1, max_length=4096, pattern=r"^/")
    environment_keys: tuple[str, ...] = ()
    deadline_at: datetime

    @model_validator(mode="after")
    def validate_environment_and_deadline(self) -> CreatePythonSessionModel:
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        if any(
            not name or "=" in name or "\x00" in name for name in self.environment_keys
        ):
            raise ValueError("environment variable name is invalid")
        if len(self.environment_keys) != len(set(self.environment_keys)):
            raise ValueError("environment variable names must be unique")
        return self

    def to_domain(self, session_id: UUID) -> CreatePythonSessionRequest:
        return CreatePythonSessionRequest(
            session_id=session_id,
            cwd=self.cwd,
            environment_keys=self.environment_keys,
            deadline_at=self.deadline_at,
        )


class PythonSessionResponse(StrictApiModel):
    session_id: UUID
    allocation_id: UUID
    allocation_epoch: int = Field(ge=1)
    cwd: str
    environment_keys: tuple[str, ...]
    state: PythonSessionState

    @classmethod
    def from_domain(cls, session: PythonSessionRef) -> PythonSessionResponse:
        return cls(
            session_id=session.session_id,
            allocation_id=session.allocation_id,
            allocation_epoch=session.allocation_epoch,
            cwd=session.cwd,
            environment_keys=session.environment_keys,
            state=session.state,
        )


class ExecutePythonModel(StrictApiModel):
    operation_id: UUID
    code: str = Field(min_length=1, max_length=4 * 1024 * 1024)
    environment: tuple[EnvironmentVariableModel, ...] = ()
    output_limit_bytes: int = Field(default=1024 * 1024, ge=1, le=2 * 1024 * 1024)
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value

    def to_domain(self) -> ExecutePythonRequest:
        return ExecutePythonRequest(
            operation_id=self.operation_id,
            code=self.code,
            environment=tuple(item.to_domain() for item in self.environment),
            output_limit_bytes=self.output_limit_bytes,
            deadline_at=self.deadline_at,
        )


class PythonResultResponse(StrictApiModel):
    operation_id: UUID
    state: PythonExecutionState
    stdout: str
    stderr: str
    result: str | None
    error_name: str | None
    error_message: str | None
    traceback: str | None
    output_truncated: bool

    @classmethod
    def from_domain(cls, result: PythonResult) -> PythonResultResponse:
        return cls(
            operation_id=result.operation_id,
            state=result.state,
            stdout=result.stdout,
            stderr=result.stderr,
            result=result.result,
            error_name=result.error_name,
            error_message=result.error_message,
            traceback=result.traceback,
            output_truncated=result.output_truncated,
        )


class DeadlineRequest(StrictApiModel):
    deadline_at: datetime

    @field_validator("deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include a timezone")
        return value


class CreatePortAccessRequest(StrictApiModel):
    protocol: PortProtocol = PortProtocol.HTTP
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def require_absolute_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value


class PortAccessResponse(StrictApiModel):
    workload_kind: WorkloadKind
    logical_id: UUID
    port: int = Field(ge=1, le=65535)
    protocol: PortProtocol
    url: str
    expires_at: datetime

    @classmethod
    def from_domain(cls, grant: PortAccessGrant) -> PortAccessResponse:
        return cls(
            workload_kind=grant.key.workload_kind,
            logical_id=grant.key.logical_id,
            port=grant.port,
            protocol=grant.protocol,
            url=grant.url,
            expires_at=grant.expires_at,
        )


class FunctionRuntimeLeaseRequest(StrictApiModel):
    required_valid_until: datetime
    deadline_at: datetime

    @field_validator("required_valid_until", "deadline_at")
    @classmethod
    def require_absolute_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("function runtime lease deadlines must include a timezone")
        return value


class RuntimeRequestHeaderResponse(StrictApiModel):
    name: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$",
    )
    value: str = Field(max_length=65536, repr=False)

    @field_validator("value")
    @classmethod
    def reject_header_injection(cls, value: str) -> str:
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ValueError("runtime request header value contains control characters")
        return value


class CapacityErrorContextModel(StrictApiModel):
    kind: Literal["capacity"]
    provider_scope: str
    active: int
    reserved: int
    limit: int


class OperationConflictContextModel(StrictApiModel):
    kind: Literal["operation_conflict"]
    operation_id: UUID


class ProviderErrorContextModel(StrictApiModel):
    kind: Literal["provider"]
    provider_name: str
    provider_request_id: str | None = None


class AllocationErrorContextModel(StrictApiModel):
    kind: Literal["allocation"]
    allocation_id: UUID | None
    allocation_epoch: int | None


class ProcessErrorContextModel(StrictApiModel):
    kind: Literal["process"]
    operation_id: UUID


ErrorContextModel = Annotated[
    CapacityErrorContextModel
    | OperationConflictContextModel
    | ProviderErrorContextModel
    | AllocationErrorContextModel
    | ProcessErrorContextModel,
    Field(discriminator="kind"),
]


class ErrorBody(StrictApiModel):
    code: ErrorCode
    message: str
    retry: RetryDisposition
    retry_after_ms: int | None = Field(default=None, ge=0)
    context: ErrorContextModel | None = None


class ErrorResponse(StrictApiModel):
    error: ErrorBody

    @classmethod
    def from_error(cls, error: AgentBoxError) -> ErrorResponse:
        context: ErrorContextModel | None = None
        if isinstance(error.context, CapacityErrorContext):
            context = CapacityErrorContextModel(
                kind="capacity",
                provider_scope=error.context.provider_scope,
                active=error.context.active,
                reserved=error.context.reserved,
                limit=error.context.limit,
            )
        elif isinstance(error.context, OperationConflictContext):
            context = OperationConflictContextModel(
                kind="operation_conflict",
                operation_id=error.context.operation_id,
            )
        elif isinstance(error.context, ProviderErrorContext):
            context = ProviderErrorContextModel(
                kind="provider",
                provider_name=error.context.provider_name,
                provider_request_id=error.context.provider_request_id,
            )
        elif isinstance(error.context, AllocationErrorContext):
            context = AllocationErrorContextModel(
                kind="allocation",
                allocation_id=error.context.allocation_id,
                allocation_epoch=error.context.allocation_epoch,
            )
        elif isinstance(error.context, ProcessErrorContext):
            context = ProcessErrorContextModel(
                kind="process",
                operation_id=error.context.operation_id,
            )
        return cls(
            error=ErrorBody(
                code=error.code,
                message=error.message,
                retry=error.retry,
                retry_after_ms=error.retry_after_ms,
                context=context,
            )
        )

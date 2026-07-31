from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from agentbox.api.contracts import (
    EnvironmentVariableModel,
    StartProcessModel,
    StrictApiModel,
)
from agentbox.domain import (
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileKind,
    FileStat,
    ProcessOutputChannel,
    ProcessState,
    PythonExecutionState,
    PythonResult,
)


OutputChannel = ProcessOutputChannel


class RuntimeStartProcessRequest(StartProcessModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeProcessResponse(StrictApiModel):
    operation_id: UUID
    state: ProcessState
    started_at: datetime
    completed_at: datetime | None
    exit_code: int | None
    next_output_seq: int = Field(ge=1)
    truncated_before_seq: int | None = Field(default=None, ge=1)


class RuntimeProcessListResponse(StrictApiModel):
    processes: tuple[RuntimeProcessResponse, ...]


class RuntimeResizeRequest(StrictApiModel):
    cols: int = Field(ge=1, le=1000)
    rows: int = Field(ge=1, le=1000)


class RuntimeTerminateRequest(StrictApiModel):
    grace_seconds: float = Field(default=5, ge=0, le=30)


class RuntimeHealthResponse(StrictApiModel):
    status: str
    managed_processes: int = Field(ge=0)
    active_python_sessions: int = Field(ge=0)


class RuntimeQuiesceResponse(StrictApiModel):
    terminated_processes: int = Field(ge=0)
    terminated_python_sessions: int = Field(ge=0)


class RuntimeFileStatResponse(StrictApiModel):
    path: str
    kind: FileKind
    size_bytes: int = Field(ge=0)
    modified_at: datetime
    mode: int = Field(ge=0)
    sha256: str | None = None

    @classmethod
    def from_domain(cls, stat: FileStat) -> RuntimeFileStatResponse:
        return cls(
            path=stat.path,
            kind=stat.kind,
            size_bytes=stat.size_bytes,
            modified_at=stat.modified_at,
            mode=stat.mode,
            sha256=stat.sha256,
        )

    def to_domain(self) -> FileStat:
        return FileStat(
            path=self.path,
            kind=self.kind,
            size_bytes=self.size_bytes,
            modified_at=self.modified_at,
            mode=self.mode,
            sha256=self.sha256,
        )


class RuntimeFileListResponse(StrictApiModel):
    entries: tuple[RuntimeFileStatResponse, ...]


class RuntimeMoveFileRequest(StrictApiModel):
    source: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    destination: str = Field(min_length=1, max_length=4096, pattern=r"^/")


class RuntimeCreatePythonSessionRequest(StrictApiModel):
    cwd: str = Field(min_length=1, max_length=4096, pattern=r"^/")
    environment_keys: tuple[str, ...] = ()
    deadline_at: datetime

    def to_domain(self, session_id: UUID) -> CreatePythonSessionRequest:
        return CreatePythonSessionRequest(
            session_id=session_id,
            cwd=self.cwd,
            environment_keys=self.environment_keys,
            deadline_at=self.deadline_at,
        )


class RuntimePythonSessionResponse(StrictApiModel):
    session_id: UUID
    cwd: str
    environment_keys: tuple[str, ...]


class RuntimeExecutePythonRequest(StrictApiModel):
    operation_id: UUID
    code: str = Field(min_length=1, max_length=4 * 1024 * 1024)
    environment: tuple[EnvironmentVariableModel, ...] = ()
    output_limit_bytes: int = Field(
        default=1024 * 1024,
        ge=1,
        le=2 * 1024 * 1024,
    )
    deadline_at: datetime

    def to_domain(self) -> ExecutePythonRequest:
        return ExecutePythonRequest(
            operation_id=self.operation_id,
            code=self.code,
            environment=tuple(item.to_domain() for item in self.environment),
            output_limit_bytes=self.output_limit_bytes,
            deadline_at=self.deadline_at,
        )


class RuntimePythonResultResponse(StrictApiModel):
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
    def from_domain(cls, result: PythonResult) -> RuntimePythonResultResponse:
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

    def to_domain(self) -> PythonResult:
        return PythonResult(
            operation_id=self.operation_id,
            state=self.state,
            stdout=self.stdout,
            stderr=self.stderr,
            result=self.result,
            error_name=self.error_name,
            error_message=self.error_message,
            traceback=self.traceback,
            output_truncated=self.output_truncated,
        )

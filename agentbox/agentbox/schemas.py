from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SandboxLifecycleStatus = Literal["CREATING", "RUNNING", "STOPPED", "ERROR"]
ExecutionStatus = Literal["completed", "error"]


class SandboxEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)


class SandboxSummary(BaseModel):
    id: str
    ready: bool
    status: SandboxLifecycleStatus


class SandboxResponse(BaseModel):
    sandbox: SandboxSummary


class DeleteResponse(BaseModel):
    sandbox_id: str
    deleted: bool


class SuspendResponse(BaseModel):
    sandbox_id: str
    suspended: bool


class SandboxHeartbeatResponse(BaseModel):
    sandbox_id: str
    active: bool


class RuntimeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = "/workspace"


class RuntimeSessionResponse(BaseModel):
    sandbox_id: str
    session_id: str
    cwd: str
    env_keys: list[str] = Field(default_factory=list)


class RuntimeSessionHeartbeatResponse(BaseModel):
    sandbox_id: str
    session_id: str
    active: bool


class ExecutePythonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    timeout_seconds: int = Field(default=60, ge=1, le=600)


class ExecutePythonResponse(BaseModel):
    sandbox_id: str
    session_id: str
    stdout: str
    stderr: str
    result: str | None = None
    error_name: str | None = None
    exit_code: int | None = None
    status: ExecutionStatus


class ExecCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cmd: str
    max_output_tokens: int | None = Field(default=None, ge=1)
    tty: bool = False
    workdir: str | None = None
    yield_time_ms: int | None = Field(default=None, ge=0)
    timeout: int | None = Field(default=300, ge=1, le=3600)


class WriteStdinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_id: str
    chars: str | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
    yield_time_ms: int | None = Field(default=None, ge=0)


class ExecCommandResponse(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    completed: bool = True
    process_id: str | None = None
    error: str | None = None


class RuntimeProcessInfo(BaseModel):
    process_id: str
    cmd: str
    cwd: str
    tty: bool = False
    started_at: float
    completed: bool = False
    exit_code: int | None = None


class ListProcessesResponse(BaseModel):
    processes: list[RuntimeProcessInfo] = Field(default_factory=list)


class AppAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int = Field(default=1800, ge=60, le=3600)


class AppAccessResponse(BaseModel):
    sandbox_id: str
    app: str
    url: str
    expires_at: int


class SandboxInternalAppStatus(BaseModel):
    name: str
    public_slug: str
    port: int
    ready: bool
    private_url: str | None = None


class SandboxInternalStatus(BaseModel):
    id: str
    ready: bool
    status: SandboxLifecycleStatus
    runtime_url: str | None = None
    pod_ip: str | None = None
    apps: dict[str, SandboxInternalAppStatus] = Field(default_factory=dict)


def sandbox_summary(status: SandboxInternalStatus) -> SandboxSummary:
    return SandboxSummary(id=status.id, ready=status.ready, status=status.status)

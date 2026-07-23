from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeEnvironmentVariable(RuntimeModel):
    name: str
    value: str


class ProcessManifest(RuntimeModel):
    operation_id: UUID
    shell_command: str | None = None
    argv: tuple[str, ...] | None = None
    cwd: str
    environment: tuple[RuntimeEnvironmentVariable, ...] = ()
    output_limit_bytes: int
    deadline_at: datetime

    @model_validator(mode="after")
    def validate_command(self) -> ProcessManifest:
        if (self.shell_command is None) == (self.argv is None):
            raise ValueError("exactly one command representation is required")
        return self


class SupervisorState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ProcessStateRecord(RuntimeModel):
    operation_id: UUID
    state: SupervisorState
    supervisor_pid: int
    child_process_group_id: int | None = None
    next_sequence: int
    truncated_before_sequence: int | None = None
    exit_code: int | None = None


class CancelRequest(RuntimeModel):
    grace_seconds: float


class RuntimeOutputChunk(RuntimeModel):
    sequence: int
    channel: str
    data_base64: str


class ProcessInspection(RuntimeModel):
    state: ProcessStateRecord | None
    chunks: tuple[RuntimeOutputChunk, ...] = ()

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .types import JsonObject


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeIdentity(RuntimeModel):
    user_id: UUID
    user_email: str | None = None
    pod_id: UUID
    function_id: UUID
    function_name: str
    organization_id: UUID | None = None


class RunClaim(RuntimeModel):
    run_id: UUID
    callback_token: str = Field(min_length=32)
    artifact_url: str
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_data: JsonObject
    config: JsonObject | None = None
    identity: RuntimeIdentity
    lemma_token: str
    lemma_base_url: str
    deadline_at: datetime


class RunAccepted(RuntimeModel):
    accepted: Literal[True] = True
    run_id: UUID


class FunctionArtifactManifest(RuntimeModel):
    format_version: Literal[1] = 1
    runtime_abi: str
    builder_digest: str
    dependency_lock: tuple[str, ...] = ()
    source_path: str = "function.py"
    input_model: str
    output_model: str
    entrypoint: str
    config_model: str | None = None
    dependency_path: str | None = None


class WorkerRequest(RuntimeModel):
    artifact_root: str
    manifest: FunctionArtifactManifest
    run_id: UUID
    input_data: JsonObject
    config: JsonObject | None
    identity: RuntimeIdentity
    lemma_token: str
    lemma_base_url: str


class RuntimeFailure(RuntimeModel):
    name: str
    message: str
    traceback: tuple[str, ...] = ()


class WorkerResult(RuntimeModel):
    ok: bool
    output_data: JsonObject | None = None
    error: RuntimeFailure | None = None

    def model_post_init(self, _context: Any) -> None:
        if self.ok == (self.error is not None):
            raise ValueError("successful results cannot contain an error")


class WorkerResponse(WorkerResult):
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    user_code_ms: float = Field(default=0, ge=0)


class WorkerReady(RuntimeModel):
    ready: bool
    error: RuntimeFailure | None = None

    def model_post_init(self, _context: Any) -> None:
        if self.ready == (self.error is not None):
            raise ValueError("ready workers cannot contain an error")


class RuntimeTimings(RuntimeModel):
    total_ms: float = Field(default=0, ge=0)
    claim_ms: float = Field(default=0, ge=0)
    artifact_ms: float = Field(default=0, ge=0)
    worker_ms: float = Field(default=0, ge=0)
    user_code_ms: float = Field(default=0, ge=0)
    artifact_cache_hit: bool = False
    queue_wait_ms: float | None = Field(default=None, ge=0)
    sandbox_start_ms: float | None = Field(default=None, ge=0)
    execution_mode: Literal["synchronous", "asynchronous"] = "synchronous"
    runtime_profile: str = Field(default="unknown", pattern=r"^[A-Za-z0-9_.-]{1,128}$")


class DispatchTimings(RuntimeModel):
    queue_wait_ms: float | None = Field(default=None, ge=0)
    sandbox_start_ms: float | None = Field(default=None, ge=0)
    execution_mode: Literal["synchronous", "asynchronous"]
    runtime_profile: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")


class TerminalReport(RuntimeModel):
    status: Literal["completed", "failed"]
    output_data: JsonObject | None = None
    error: RuntimeFailure | None = None
    stdout: str
    stderr: str
    output_truncated: bool = False
    timings: RuntimeTimings = Field(default_factory=RuntimeTimings)

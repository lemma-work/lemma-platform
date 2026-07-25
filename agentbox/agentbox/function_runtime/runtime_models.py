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


class FunctionSchemaSet(RuntimeModel):
    input: JsonObject
    output: JsonObject
    config: JsonObject | None = None


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


class SchemaInspection(RuntimeModel):
    ok: bool
    schemas: FunctionSchemaSet | None = None
    error: RuntimeFailure | None = None

    def model_post_init(self, _context: Any) -> None:
        if self.ok != (self.schemas is not None) or self.ok == (self.error is not None):
            raise ValueError("schema inspection result is inconsistent")


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


class WorkerReady(RuntimeModel):
    ready: bool
    error: RuntimeFailure | None = None

    def model_post_init(self, _context: Any) -> None:
        if self.ready == (self.error is not None):
            raise ValueError("ready workers cannot contain an error")


class TerminalReport(RuntimeModel):
    status: Literal["completed", "failed"]
    output_data: JsonObject | None = None
    error: RuntimeFailure | None = None
    stdout: str
    stderr: str
    output_truncated: bool = False

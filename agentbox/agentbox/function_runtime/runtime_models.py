from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeIdentity(RuntimeModel):
    user_id: UUID
    user_email: str | None = None
    pod_id: UUID
    function_id: UUID
    function_name: str
    organization_id: UUID | None = None


class AttemptClaim(RuntimeModel):
    attempt_id: UUID
    fence: int = Field(ge=1)
    runtime_token: str = Field(min_length=32)
    artifact_url: str
    artifact_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_data: dict[str, Any]
    config: dict[str, Any] | None = None
    identity: RuntimeIdentity
    lemma_token: str
    lemma_base_url: str
    deadline_at: datetime


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
    attempt_id: UUID
    input_data: dict[str, Any]
    config: dict[str, Any] | None
    identity: RuntimeIdentity
    lemma_token: str
    lemma_base_url: str


class RuntimeFailure(RuntimeModel):
    name: str
    message: str
    traceback: tuple[str, ...] = ()


class WorkerResult(RuntimeModel):
    ok: bool
    output_data: dict[str, Any] | None = None
    error: RuntimeFailure | None = None

    def model_post_init(self, _context: Any) -> None:
        if self.ok == (self.error is not None):
            raise ValueError("successful results cannot contain an error")


class TerminalReport(RuntimeModel):
    fence: int = Field(ge=1)
    status: Literal["completed", "failed"]
    output_data: dict[str, Any] | None = None
    error: RuntimeFailure | None = None
    stdout: str
    stderr: str
    output_truncated: bool = False

"""Strict wire contracts between the backend and the sandbox function runner."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.function.domain.types import JsonObject


class RuntimeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeClaimRequest(RuntimeContract):
    revision_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_data: JsonObject


class RuntimeIdentity(RuntimeContract):
    user_id: UUID
    user_email: str | None = None
    pod_id: UUID
    function_id: UUID
    function_name: str
    organization_id: UUID | None = None


class RuntimeClaimResponse(RuntimeContract):
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


class RuntimeAcceptedResponse(RuntimeContract):
    accepted: Literal[True] = True
    run_id: UUID


class RuntimeFailure(RuntimeContract):
    name: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=16_384)
    traceback: tuple[str, ...] = Field(default=(), max_length=256)


class RuntimeTimings(RuntimeContract):
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


class RuntimeTerminalRequest(RuntimeContract):
    status: Literal["completed", "failed"]
    output_data: JsonObject | None = None
    error: RuntimeFailure | None = None
    stdout: str = Field(max_length=4 * 1024 * 1024)
    stderr: str = Field(max_length=4 * 1024 * 1024)
    output_truncated: bool = False
    timings: RuntimeTimings = Field(default_factory=RuntimeTimings)

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> RuntimeTerminalRequest:
        if self.status == "completed" and self.error is not None:
            raise ValueError("completed execution cannot contain an error")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed execution requires an error")
        return self


class RuntimeEventResponse(RuntimeContract):
    accepted: bool
    duplicate: bool = False

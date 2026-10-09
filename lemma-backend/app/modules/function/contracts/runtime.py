"""Strict wire contracts between the backend and the sandbox function runner."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.function.domain.types import JsonObject


class RuntimeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeIdentity(RuntimeContract):
    #: ``None`` on a run started for a contact, which acts for no member.
    user_id: UUID | None
    user_email: str | None = None
    #: Sent only when set. A runtime that predates contacts forbids unknown
    #: fields, and a member's run must keep reaching it unchanged.
    contact_id: UUID | None = None
    pod_id: UUID
    function_id: UUID
    function_name: str
    organization_id: UUID | None = None


class RuntimeInvocationRequest(RuntimeContract):
    protocol_version: Literal[2] = 2
    input: JsonObject
    config: JsonObject | None = None
    identity: RuntimeIdentity
    lemma_base_url: str
    deadline_at: datetime


def invocation_payload(body: RuntimeInvocationRequest) -> dict[str, object]:
    """The body as sent. ``identity.contact_id`` only when there is one: an
    older runtime forbids the field, and a member's run must still reach it
    unchanged."""
    exclude = {"identity": {"contact_id"}} if body.identity.contact_id is None else None
    return body.model_dump(mode="json", exclude=exclude)


class RuntimeAcceptedResponse(RuntimeContract):
    accepted: Literal[True] = True
    run_id: UUID


class RuntimeFailure(RuntimeContract):
    name: str = Field(min_length=1, max_length=256)
    message: str = Field(max_length=16_384)
    traceback: tuple[str, ...] = Field(default=(), max_length=256)


class RuntimeFunctionSchemaSet(RuntimeContract):
    input: JsonObject
    output: JsonObject
    config: JsonObject | None = None


class RuntimeSchemaInspection(RuntimeContract):
    ok: bool
    schemas: RuntimeFunctionSchemaSet | None = None
    error: RuntimeFailure | None = None

    @model_validator(mode="after")
    def validate_inspection_shape(self) -> RuntimeSchemaInspection:
        if self.ok != (self.schemas is not None) or self.ok == (self.error is not None):
            raise ValueError("schema inspection result is inconsistent")
        return self


class RuntimeTerminalRequest(RuntimeContract):
    status: Literal["completed", "failed"]
    output_data: JsonObject | None = None
    error: RuntimeFailure | None = None
    stdout: str = Field(max_length=4 * 1024 * 1024)
    stderr: str = Field(max_length=4 * 1024 * 1024)
    output_truncated: bool = False

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

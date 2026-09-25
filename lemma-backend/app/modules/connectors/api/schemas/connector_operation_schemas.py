from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class OperationSummary(BaseModel):
    """Compact operation metadata for discovery flows."""

    name: str
    description: Optional[str] = None
    relevance_score: Optional[float] = Field(
        default=None,
        ge=0,
        le=1,
        description="Relative relevance for the discovery query, from 0 to 1.",
    )
    # Set only by the org-wide search, where hits span installs and the caller
    # has no other way to know which one to execute against.
    auth_config: Optional[str] = Field(
        default=None,
        description="Install this operation belongs to (org-wide search only).",
    )
    connector_id: Optional[str] = Field(
        default=None,
        description="Connector this operation belongs to (org-wide search only).",
    )


class OperationDetail(BaseModel):
    """Full operation metadata including input and output schemas."""

    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]] = None

    @field_validator("input_schema")
    @classmethod
    def _files_as_callers_pass_them(
        cls, schema: dict[str, object]
    ) -> dict[str, object]:
        """File arguments as a caller passes them -- a pod path, a file id --
        not as each provider receives them. Composio's `{name, mimetype,
        s3key}` in particular is something no caller can produce. Here rather
        than in the service so every surface that describes an operation --
        the API, the SDKs, the agent's tools -- shows the same thing."""
        from app.modules.connectors.services.files.file_ref import (
            present_input_schema,
        )

        return present_input_schema(schema)


class OperationDiscoverResponse(BaseModel):
    """Structured result for operation discovery within one connector."""

    connector_id: str = Field(description="Connector identifier.")
    query: str | None = Field(
        default=None,
        description="Optional discovery query used to rank or filter operations.",
    )
    items: list[OperationSummary] = Field(
        description="Matching operations with compact descriptions."
    )
    total_operations: int = Field(
        description="Total operations available for the connector."
    )
    returned_count: int = Field(
        description="Number of operations returned in this response."
    )


#: The most operation details one call may return. A detail carries the
#: operation's whole input and output JSON Schema -- for a catalog the size of
#: Jira's, "every operation" is tens of megabytes of JSON assembled in memory,
#: which any org member could ask for repeatedly. Matches the `le=1000` the
#: discovery endpoint next door already bounds itself by.
MAX_OPERATION_DETAILS_PER_REQUEST = 1000


class OperationDetailsBatchRequest(BaseModel):
    """Request multiple operation details in a single call."""

    operation_names: list[str] | None = Field(
        default=None,
        max_length=MAX_OPERATION_DETAILS_PER_REQUEST,
        description=(
            "Operation names to fetch. Omit or pass an empty list to return "
            "details for the first `limit` operations in the connector; read "
            "`total_operations` on the response to see whether that was all "
            "of them."
        ),
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=MAX_OPERATION_DETAILS_PER_REQUEST,
        description=(
            "How many to return when `operation_names` is omitted. Ignored "
            "when names are given."
        ),
    )


class OperationDetailsBatchResponse(BaseModel):
    """Batch response containing full metadata for multiple operations."""

    connector_id: str = Field(description="Connector identifier.")
    items: list[OperationDetail] = Field(
        description="Operation details for the requested operations."
    )
    returned_count: int = Field(
        description="Number of operation details returned in this response."
    )
    total_operations: int = Field(
        default=0,
        description=(
            "Operations the connector exposes in total. Greater than "
            "`returned_count` means the unnamed request was capped."
        ),
    )


class OperationExecutionRequest(BaseModel):
    payload: Dict[str, Any] = Field(
        description=(
            "The operation's arguments. A file argument takes a reference -- "
            '`{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, '
            '`{"url": "https://..."}` or `{"base64": "...", "filename": "..."}` '
            "-- read with the caller's own access before the call is made. "
            "`output_path` chooses where a file result lands in the pod."
        )
    )
    account_id: str | None = None
    pod_id: UUID | None = Field(
        default=None,
        description=(
            "The pod that `pod_path` and `file_id` references resolve in, and "
            "that file results land in. Implied for a call made from inside a "
            "pod; name it when calling as a person from outside one."
        ),
    )


class OperationExecutionResponse(BaseModel):
    result: Any

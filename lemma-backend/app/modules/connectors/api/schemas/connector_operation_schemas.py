from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


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


class OperationDetailsBatchRequest(BaseModel):
    """Request multiple operation details in a single call."""

    operation_names: list[str] | None = Field(
        default=None,
        description=(
            "Operation names to fetch. Omit or pass an empty list to return "
            "details for every operation in the connector."
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


class OperationExecutionRequest(BaseModel):
    payload: Dict[str, Any]
    account_id: str | None = None


class OperationExecutionResponse(BaseModel):
    result: Any

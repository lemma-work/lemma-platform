from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.authorization.context import ResourceType, ResourceVisibility
from app.core.authorization.grants import ensure_grant_uses_resource_name
from app.modules.function.domain.entities import (
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.domain.types import JsonObject


class FunctionResourcePermissionRequest(BaseModel):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _require_resource_name(cls, data: object) -> object:
        return ensure_grant_uses_resource_name(data)


class FunctionPermissionsReplaceRequest(BaseModel):
    grants: list[FunctionResourcePermissionRequest] = Field(default_factory=list)


class CreateFunctionRequest(BaseModel):
    """Request to create a function.

    Input and output schemas are derived from the submitted code and returned
    on the function response. They are not accepted in create requests.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    icon_url: str | None = None
    config: JsonObject | None = None
    code: str | None = Field(
        default=None,
        description=(
            "Python source for the function. When provided, the platform analyzes "
            "the code and populates input_schema, output_schema, and config_schema "
            "on the returned function."
        ),
    )
    type: FunctionType = FunctionType.API
    visibility: ResourceVisibility = ResourceVisibility.POD
    permissions: FunctionPermissionsReplaceRequest | None = Field(
        default=None,
        description=(
            "Optional resource grants to REPLACE on this function, in the same "
            "request. Equivalent to calling the permissions-replace endpoint "
            "right after this call — grants are keyed by resource_name. Omit "
            "the key to leave existing grants alone; an empty grant list "
            "revokes them."
        ),
    )


class UpdateFunctionRequest(BaseModel):
    """Request to update a function."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    icon_url: str | None = None
    config: JsonObject | None = None
    code: str | None = Field(
        default=None,
        description=(
            "Updated Python source for the function. When provided, the platform "
            "re-analyzes the code and refreshes input_schema, output_schema, and "
            "config_schema on the returned function."
        ),
    )
    type: FunctionType | None = None
    visibility: ResourceVisibility | None = None
    permissions: FunctionPermissionsReplaceRequest | None = Field(
        default=None,
        description=(
            "Optional resource grants to REPLACE on this function, in the same "
            "request. Equivalent to calling the permissions-replace endpoint "
            "right after this call — grants are keyed by resource_name. Omit "
            "the key to leave existing grants alone; an empty grant list "
            "revokes them."
        ),
    )


class ExecuteFunctionRequest(BaseModel):
    """Request to execute a function."""

    model_config = ConfigDict(extra="forbid")

    input_data: JsonObject = Field(default_factory=dict)


class FunctionResourcePermissionResponse(BaseModel):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)


class FunctionPermissionsResponse(BaseModel):
    function_id: UUID
    function_name: str
    grants: list[FunctionResourcePermissionResponse] = Field(default_factory=list)


class FunctionResponse(BaseModel):
    """Function response."""

    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    input_schema: JsonObject = Field(
        description="Input JSON schema derived from the function code."
    )
    output_schema: JsonObject = Field(
        description="Output JSON schema derived from the function code."
    )
    config_schema: JsonObject | None = Field(
        default=None,
        description="Optional configuration schema derived from the function code.",
    )
    config: JsonObject | None = None
    type: FunctionType
    status: FunctionStatus
    visibility: str = "POD"
    code_path: str | None = None
    revision_hash: str | None = None
    code: str | None = (
        None  # Include code content if requested? Controller usually handles this.
    )
    created_at: datetime | None
    updated_at: datetime | None

    model_config = {"from_attributes": True}


class FunctionActionResponse(FunctionResponse):
    allowed_actions: list[str] = Field(default_factory=list)


class FunctionDetailResponse(FunctionActionResponse):
    permissions: FunctionPermissionsResponse


class FunctionSummaryResponse(BaseModel):
    """Lean function shape for list responses.

    Omits the heavy `input_schema` / `output_schema` / `config_schema` (full JSON
    schemas derived from the function code) and `code` — fetch those from
    `function.get`.
    """

    model_config = {"from_attributes": True}

    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    config: JsonObject | None = None
    type: FunctionType
    status: FunctionStatus
    visibility: str = "POD"
    code_path: str | None = None
    revision_hash: str | None = None
    created_at: datetime | None
    updated_at: datetime | None
    allowed_actions: list[str] = Field(default_factory=list)
    # Populated only for `?include=permissions`. None means "not requested"; an
    # empty list means "holds no grants" — a zero-grant workload is the most
    # common reason a fresh pod 403s, so those must be distinguishable. Costs
    # ONE extra query for the whole page, not one per function.
    grants: list[FunctionResourcePermissionResponse] | None = None


class FunctionListResponse(BaseModel):
    """List of functions."""

    items: list[FunctionSummaryResponse]
    limit: int
    next_page_token: str | None = None


class FunctionRunResponse(BaseModel):
    """Function run response."""

    id: UUID
    function_id: UUID
    revision_hash: str | None = None
    user_id: UUID
    input_data: JsonObject | None = None
    output_data: JsonObject | None = None
    status: FunctionRunStatus
    user_email: str | None = None
    job_id: str | None = None
    error: str | None = None
    logs: str | None = None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


class FunctionRunSummaryResponse(BaseModel):
    """Function run summary for list responses."""

    id: UUID
    function_id: UUID
    user_id: UUID
    status: FunctionRunStatus
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None

    model_config = {"from_attributes": True}


class FunctionRunListResponse(BaseModel):
    """List of function runs."""

    items: list[FunctionRunSummaryResponse]
    limit: int
    next_page_token: str | None = None


class FunctionMessageResponse(BaseModel):
    """Simple function action response."""

    message: str

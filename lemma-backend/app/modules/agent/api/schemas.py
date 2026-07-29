"""API schemas for agents."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.core.authorization.context import ResourceType, ResourceVisibility
from app.core.authorization.grants import ensure_grant_uses_resource_name
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentRunApprovalDecision,
    AgentRunStatus,
    AgentToolset,
    ConversationStatus,
    ConversationType,
    JsonObject,
    JsonValue,
    MessageKind,
)
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    AzureOpenAIRuntimeConfig,
    GoogleVertexRuntimeConfig,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    RuntimeProfileType,
)


class AgentResourcePermissionRequest(BaseModel):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _require_resource_name(cls, data: object) -> object:
        return ensure_grant_uses_resource_name(data)


class AgentPermissionsReplaceRequest(BaseModel):
    grants: list[AgentResourcePermissionRequest] = Field(default_factory=list)


class AgentResourcePermissionResponse(BaseModel):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)


class AgentPermissionsResponse(BaseModel):
    agent_id: UUID
    agent_name: str
    grants: list[AgentResourcePermissionResponse] = Field(default_factory=list)


class AgentResponse(BaseModel):
    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    visibility: str = "POD"
    instruction: str
    agent_runtime: AgentRuntimeConfig | None = None
    toolsets: list[AgentToolset] = Field(default_factory=list)
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    metadata: JsonObject | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentActionResponse(AgentResponse):
    allowed_actions: list[str] = Field(default_factory=list)


class AgentDetailResponse(AgentActionResponse):
    permissions: AgentPermissionsResponse


class AgentSummaryResponse(BaseModel):
    """Lean agent shape for list responses.

    Omits the heavy single-resource fields (`instruction`, `input_schema`,
    `output_schema`, `agent_runtime`) — fetch those from `agent.get`. Keeps
    `toolsets` so list cards can show a connection count.
    """

    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    description: str | None = None
    icon_url: str | None = None
    visibility: str = "POD"
    toolsets: list[AgentToolset] = Field(default_factory=list)
    metadata: JsonObject | None = None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class AgentListResponse(BaseModel):
    items: list[AgentSummaryResponse]
    limit: int
    next_page_token: str | None = None


class ConversationResponse(BaseModel):
    id: UUID
    user_id: UUID
    pod_id: UUID
    organization_id: UUID | None = None
    agent_id: UUID | None = None
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    parent_id: UUID | None = None
    type: ConversationType = ConversationType.CHAT
    status: ConversationStatus | None = None
    output: JsonValue | None = None
    metadata: JsonObject | None = None
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    limit: int
    next_page_token: str | None = None


class AgentRunStartResponse(BaseModel):
    conversation_id: UUID
    agent_run_id: UUID
    started_new_run: bool

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence: int
    agent_run_id: UUID | None = None
    role: str
    kind: MessageKind
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: JsonValue | None = None
    tool_result: JsonValue | None = None
    metadata: JsonObject | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    limit: int
    next_page_token: str | None = None


class UserApprovalListResponse(BaseModel):
    items: list[MessageResponse]


class ResolveUserApprovalRequest(BaseModel):
    decision: AgentRunApprovalDecision
    response: JsonObject | None = None


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    decision: AgentRunApprovalDecision
    status: str = "resolved"


class CreateConversationRequest(BaseModel):
    agent_name: str | None = None
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    parent_id: UUID | None = None
    type: ConversationType = ConversationType.CHAT
    metadata: JsonObject | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    metadata: JsonObject | None = None


class SendMessageRequest(BaseModel):
    content: str
    metadata: JsonObject | None = None


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    instruction: str = Field(min_length=1)
    description: str | None = None
    icon_url: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    toolsets: list[AgentToolset] = Field(default_factory=list)
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    visibility: ResourceVisibility = ResourceVisibility.POD
    metadata: JsonObject | None = None
    permissions: AgentPermissionsReplaceRequest | None = Field(
        default=None,
        description=(
            "Optional resource grants to apply to the new agent in the same "
            "request. Equivalent to calling the permissions-replace endpoint "
            "right after create — grants are keyed by resource_name."
        ),
    )


class UpdateAgentRequest(BaseModel):
    instruction: str | None = Field(default=None, min_length=1)
    description: str | None = None
    icon_url: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    toolsets: list[AgentToolset] | None = None
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    visibility: ResourceVisibility | None = None
    metadata: JsonObject | None = None


class AgentMessageResponse(BaseModel):
    message: str


class RuntimeProfileResponseBase(BaseModel):
    id: str
    organization_id: UUID | None = None
    owner_user_id: UUID | None = None
    scope: RuntimeProfileScope
    name: str
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    status: RuntimeProfileStatus
    has_credentials: bool = False
    availability_status: str | None = None


class OpenAICompatibleRuntimeProfileResponse(RuntimeProfileResponseBase):
    runtime_type: Literal[RuntimeProfileType.OPENAI_COMPATIBLE]
    config: OpenAICompatibleRuntimeConfig | None


class AnthropicCompatibleRuntimeProfileResponse(RuntimeProfileResponseBase):
    runtime_type: Literal[RuntimeProfileType.ANTHROPIC_COMPATIBLE]
    config: AnthropicCompatibleRuntimeConfig | None


class AzureOpenAIRuntimeProfileResponse(RuntimeProfileResponseBase):
    runtime_type: Literal[RuntimeProfileType.AZURE_OPENAI]
    config: AzureOpenAIRuntimeConfig | None


class GoogleVertexRuntimeProfileResponse(RuntimeProfileResponseBase):
    runtime_type: Literal[RuntimeProfileType.GOOGLE_VERTEX]
    config: GoogleVertexRuntimeConfig | None


class HarnessRuntimeProfileResponse(RuntimeProfileResponseBase):
    runtime_type: Literal[RuntimeProfileType.HARNESS]
    config: HarnessRuntimeConfig
    harness_id: UUID
    host_id: UUID | None = None
    host_display_name: str | None = None
    host_status: str | None = None
    harness_key: str | None = None
    harness_health: str | None = None
    harness_config_revision: str | None = None


AgentRuntimeProfileResponse = Annotated[
    OpenAICompatibleRuntimeProfileResponse
    | AnthropicCompatibleRuntimeProfileResponse
    | AzureOpenAIRuntimeProfileResponse
    | GoogleVertexRuntimeProfileResponse
    | HarnessRuntimeProfileResponse,
    Field(discriminator="runtime_type"),
]


class AgentRuntimeProfileListResponse(BaseModel):
    items: list[AgentRuntimeProfileResponse]
    default_runtime: AgentRuntimeConfig


class CreateOpenAICompatibleRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_type: Literal[RuntimeProfileType.OPENAI_COMPATIBLE]
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateAnthropicCompatibleRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_type: Literal[RuntimeProfileType.ANTHROPIC_COMPATIBLE]
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    base_url: HttpUrl | None = None
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateAzureOpenAIRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_type: Literal[RuntimeProfileType.AZURE_OPENAI]
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    azure_endpoint: HttpUrl
    api_version: str | None = Field(default=None, min_length=1)
    api_key: str = Field(min_length=1)
    description: str | None = None
    default_model_name: str = Field(min_length=1)
    model_names: list[str] = Field(min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateGoogleVertexRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_type: Literal[RuntimeProfileType.GOOGLE_VERTEX]
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    project_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    service_account_json: JsonObject | None = None
    description: str | None = None
    default_model_name: str = Field(min_length=1)
    model_names: list[str] = Field(min_length=1)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateHarnessRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_type: Literal[RuntimeProfileType.HARNESS]
    harness_id: UUID
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    harness_snapshot_revision: str = Field(min_length=1, max_length=255)
    config_selections: JsonObject = Field(default_factory=dict)
    host_wait_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    fallback_profile_id: str | None = Field(default=None, min_length=1)


CreateAgentRuntimeProfileRequest = Annotated[
    CreateOpenAICompatibleRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateAzureOpenAIRuntimeProfileRequest
    | CreateGoogleVertexRuntimeProfileRequest
    | CreateHarnessRuntimeProfileRequest,
    Field(discriminator="runtime_type"),
]


class UpdateRuntimeProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: RuntimeProfileStatus | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    base_url: HttpUrl | None = None
    azure_endpoint: HttpUrl | None = None
    api_version: str | None = Field(default=None, min_length=1)
    project_id: str | None = Field(default=None, min_length=1)
    location: str | None = Field(default=None, min_length=1)
    service_account_json: JsonObject | None = None
    api_key: str | None = Field(default=None, min_length=1)
    model_names: list[str] | None = None
    headers: dict[str, str] | None = None
    model_settings: JsonObject | None = None
    harness_snapshot_revision: str | None = Field(
        default=None, min_length=1, max_length=255
    )
    config_selections: JsonObject | None = None
    host_wait_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    fallback_profile_id: str | None = None


class AgentRunResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    agent_id: UUID | None = None
    parent_run_id: UUID | None = None
    status: AgentRunStatus
    agent_runtime: AgentRuntimeConfig
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    output_data: JsonValue | None = None
    metadata: JsonObject | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

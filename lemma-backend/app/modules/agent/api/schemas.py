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
    HarnessKind,
    JsonObject,
    JsonValue,
    MessageKind,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    limit: int
    next_page_token: str | None = None


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


class AgentHarnessInfo(BaseModel):
    harness_kind: HarnessKind
    display_name: str
    models: list[str] = Field(default_factory=list)
    # Structured model entries (display name, provider model id, context
    # metadata) so the picker can advertise detected models nicely — not just
    # the flat ``models`` aliases.
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    available: bool = True
    availability_status: str | None = None
    daemon_id: UUID | None = None
    daemon_display_name: str | None = None
    daemon_status: str | None = None


class AgentHarnessListResponse(BaseModel):
    items: list[AgentHarnessInfo]


class AgentRuntimeProfileResponse(BaseModel):
    id: str
    organization_id: UUID | None = None
    user_id: UUID | None = None
    daemon_id: UUID | None = None
    scope: RuntimeProfileScope
    kind: RuntimeProfileKind
    protocol: RuntimeProfileProtocol
    name: str
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    config: JsonObject = Field(default_factory=dict)
    status: RuntimeProfileStatus
    metadata: JsonObject = Field(default_factory=dict)
    has_credentials: bool = False
    derived_harness_kind: HarnessKind
    daemon_display_name: str | None = None
    daemon_status: str | None = None
    daemon_harness_available: bool | None = None
    availability_status: str | None = None


class AgentRuntimeProfileListResponse(BaseModel):
    items: list[AgentRuntimeProfileResponse]
    default_runtime: AgentRuntimeConfig


class CreateUserDaemonRuntimeProfileRequest(BaseModel):
    source: Literal["USER_DAEMON"] = "USER_DAEMON"
    daemon_id: UUID
    harness_kind: HarnessKind
    scope: RuntimeProfileScope = RuntimeProfileScope.ORGANIZATION
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)


class CreateOpenAICompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["OPENAI_COMPATIBLE"] = "OPENAI_COMPATIBLE"
    name: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateAnthropicCompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["ANTHROPIC_COMPATIBLE"] = "ANTHROPIC_COMPATIBLE"
    name: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    base_url: HttpUrl | None = None
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


CreateAgentRuntimeProfileRequest = Annotated[
    CreateUserDaemonRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest,
    Field(discriminator="source"),
]


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

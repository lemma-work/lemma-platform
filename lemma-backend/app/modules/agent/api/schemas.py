"""API schemas for agents."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    model_validator,
)

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
from app.modules.agent.services.workspace_location import pod_cwd_for
from app.modules.agent.tools.toolset_selection import NEW_AGENT_DEFAULT_TOOLSETS
from app.modules.agent.api.agent_host_schemas import AgentHostHarnessResponse
from app.modules.agent.domain.agent_host import AgentHostStatus
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
    # Populated only for `?include=permissions`. None means "not requested"; an
    # empty list means "holds no grants" — anything checking pod health has to
    # tell those apart. Requesting it costs ONE extra query for the whole page,
    # not one per agent, which is what the per-agent permissions endpoint forced
    # every caller into.
    grants: list[AgentResourcePermissionResponse] | None = None
    # Whether the agent pins a runtime profile. The full `agent_runtime` config
    # stays on the detail response; a list caller only ever asks "is one pinned?"
    # and had to fetch every agent to find out.
    has_pinned_runtime: bool = False
    # Whether the agent declares typed inputs. Same bargain as
    # `has_pinned_runtime`: the schema itself stays on the detail response, but
    # the one question every list caller asks is answerable with a boolean.
    # It is a category line, not a detail — an agent with typed inputs is
    # *called* with arguments, an agent without one is *talked to*, and a list
    # of agents someone can open a conversation with holds only the second.
    takes_input: bool = False

    model_config = ConfigDict(from_attributes=True)


class AgentListResponse(BaseModel):
    items: list[AgentSummaryResponse]
    limit: int
    next_page_token: str | None = None


class ConversationParticipantResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    #: Exactly one of these is set. A person row is who may read the
    #: conversation; an agent row is the roster a mention resolves against.
    user_id: UUID | None = None
    agent_id: UUID | None = None
    role: str
    #: What to call them on screen: a name, or an email, or nothing.
    display_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class ConversationParticipantListResponse(BaseModel):
    items: list[ConversationParticipantResponse]


class AddConversationParticipantRequest(BaseModel):
    """Add one person or one agent. Naming both, or neither, is rejected."""

    user_id: UUID | None = None
    agent_name: str | None = None


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
    is_archived: bool = False
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    #: Everyone in it, people and agents. Carried on the conversation rather
    #: than fetched separately because the transcript needs it to attribute a
    #: message the moment it renders one.
    participants: list[ConversationParticipantResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @computed_field(  # type: ignore[prop-decorator]
        return_type=str,
        description=(
            "The conversation's working directory in pod files. Anything a "
            "person attaches here is what the agent finds by a bare filename, "
            "because this is the directory its pod tools resolve against."
        ),
    )
    @property
    def pod_cwd(self) -> str:
        # Derived rather than stored: the cwd already lives in metadata, and
        # `workspace_location` owns the ladder that reads it. A client that
        # rebuilt this path itself would be a second implementation of a rule
        # the agent's tools also depend on, which is how an upload ends up
        # somewhere the agent never looks.
        return pod_cwd_for(
            metadata=self.metadata,
            conversation_id=self.id,
            created_at=self.created_at,
        )


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    limit: int
    next_page_token: str | None = None


class AgentRunStartResponse(BaseModel):
    conversation_id: UUID
    #: Null when the message was stored and no agent is answering it. See
    #: `AgentRunStartResult`.
    agent_run_id: UUID | None = None
    #: Which agent is answering. Null for the pod's default assistant.
    agent_id: UUID | None = None
    started_new_run: bool

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence: int
    agent_run_id: UUID | None = None
    #: Which person wrote this, when a person did. Null on everything an agent
    #: produced, and on user messages predating the column.
    sender_user_id: UUID | None = None
    #: The agent that produced it, when an agent did. Null on anything a person
    #: wrote. A conversation can be answered by more than one agent, so this is
    #: what lets a transcript put a name on an answer.
    agent_id: UUID | None = None
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
    #: Send an explicit null (or a blank string) to clear a title and hand the
    #: conversation back to auto-titling. An omitted field changes nothing.
    title: str | None = None
    instructions: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    metadata: JsonObject | None = None
    is_archived: bool | None = None


class SendMessageRequest(BaseModel):
    content: str
    metadata: JsonObject | None = None
    #: Address one agent for this turn, as an `@mention` does. It must already
    #: be in the conversation: naming one that is not is refused, so a name
    #: typed into a message cannot reach an agent nobody added.
    agent_name: str | None = None
    #: Branch a subthread from an earlier run. The new run sees that run and
    #: everything leading to it, and no sibling branch sees this one.
    branch_from_run_id: UUID | None = None


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    instruction: str = Field(min_length=1)
    description: str | None = None
    icon_url: str | None = None
    agent_runtime: AgentRuntimeConfig | None = None
    # Omitting toolsets means "the sensible ones", not "none" -- see
    # NEW_AGENT_DEFAULT_TOOLSETS. An explicit empty list still means none, so a
    # bundle or an editor that states the toolsets keeps stating them exactly.
    toolsets: list[AgentToolset] = Field(
        default_factory=lambda: list(NEW_AGENT_DEFAULT_TOOLSETS),
        description=(
            "Toolsets the agent declares. Omit the field to start with web "
            "search and memory; pass an explicit empty list for none."
        ),
    )
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
    permissions: AgentPermissionsReplaceRequest | None = Field(
        default=None,
        description=(
            "Optional resource grants to REPLACE on this agent, in the same "
            "request. Equivalent to calling the permissions-replace endpoint "
            "right after update — grants are keyed by resource_name. Omit the "
            "key to leave existing grants alone; an empty grant list revokes "
            "them."
        ),
    )


class AgentMessageResponse(BaseModel):
    message: str


class AgentRuntimeProfileResponse(BaseModel):
    id: str
    organization_id: UUID | None = None
    user_id: UUID | None = None
    harness_id: UUID | None = None
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
    availability_status: str | None = None


class AgentRuntimeProfileListResponse(BaseModel):
    items: list[AgentRuntimeProfileResponse]
    default_runtime: AgentRuntimeConfig


class CreateAgentHostRuntimeProfileRequest(BaseModel):
    source: Literal["AGENT_HOST"] = "AGENT_HOST"
    # From GET /me/runtime/agent-hosts/{id}/harnesses, which is what Lemma
    # Desktop lists under Models.
    harness_id: UUID
    # Personal unless asked for. Unlike a provider profile, which carries an
    # organization's own credential, this one points at a coding agent on one
    # person's machine and dispatches runs there whoever selects the model.
    # Omitting the field is not a decision to share a laptop with a workspace.
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    config_selections: JsonObject = Field(default_factory=dict)
    host_wait_timeout_seconds: int | None = Field(default=None, ge=1)


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
    CreateAgentHostRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest,
    Field(discriminator="source"),
]


# Every field below is optional AND nullable, because for an edit those mean
# different things: omitting ``api_key`` keeps the stored one, sending null
# clears it. The controller reads ``model_fields_set`` to tell them apart -
# collapsing the two would silently destroy a credential on every rename.
# ``headers`` is deliberately absent: it can carry an authorization value, so it
# needs the same absent-vs-null discipline and no UI asks for it yet.


class UpdateAgentHostRuntimeProfileRequest(BaseModel):
    source: Literal["AGENT_HOST"] = "AGENT_HOST"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    config_selections: JsonObject | None = None
    host_wait_timeout_seconds: int | None = Field(default=None, ge=1)


class UpdateOpenAICompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["OPENAI_COMPATIBLE"] = "OPENAI_COMPATIBLE"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] | None = None
    model_settings: JsonObject | None = None
    refresh_models: bool = False


class UpdateAnthropicCompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["ANTHROPIC_COMPATIBLE"] = "ANTHROPIC_COMPATIBLE"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    # Null resets to the default Anthropic endpoint - unlike the
    # OpenAI-compatible profile, which has no default to fall back to.
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] | None = None
    model_settings: JsonObject | None = None
    refresh_models: bool = False


UpdateAgentRuntimeProfileRequest = Annotated[
    UpdateAgentHostRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest,
    Field(discriminator="source"),
]


class AgentRuntimeProfileDetailResponse(AgentRuntimeProfileResponse):
    """One profile plus the live harness it is bound to.

    An editor has to render the harness's *current* config options, not the ones
    the profile was saved against - those are what the edit will be validated
    and re-pinned to.
    """

    harness: AgentHostHarnessResponse | None = None
    host_status: AgentHostStatus | None = None

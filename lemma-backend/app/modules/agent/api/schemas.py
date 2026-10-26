"""API schemas for agents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    computed_field,
    field_validator,
    model_validator,
)

from app.core.authorization.context import (
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.grants import ensure_grant_uses_resource_name
from app.modules.agent.contracts.agent_response import AgentResponse as AgentResponse
from app.modules.agent.contracts.agent_response import StoredVisibility
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.entities import (
    MAX_AGENT_INSTRUCTION_CHARACTERS,
    MAX_MESSAGE_CHARACTERS,
    Conversation,
    Message,
)
from app.modules.agent.domain.pending_interactions import PendingInteractionKind
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
from app.modules.agent.domain.wire_metadata import (
    conversation_wire_metadata,
    message_wire_metadata,
)
from app.modules.agent.domain.workspace_location import (
    default_cwd_for,
    pod_cwd_from_workspace_cwd,
    resolve_workspace_location,
)
from app.modules.agent.infrastructure.tools.toolset_selection import (
    NEW_AGENT_DEFAULT_TOOLSETS,
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
    # Which of these the pod came with. A client renders the default
    # assistant beside the agents somebody made, and has to be able to tell
    # them apart to withhold edit and delete -- it is the same list.
    kind: AgentKind = AgentKind.USER
    description: str | None = None
    icon_url: str | None = None
    visibility: StoredVisibility = ResourceVisibility.POD
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
    #: The channel it is held in, if any: a channel's conversations are
    #: reached through the channel.
    channel_id: UUID | None = None
    type: ConversationType = ConversationType.CHAT
    status: ConversationStatus | None = None
    output: JsonValue | None = None
    metadata: JsonObject | None = None
    is_archived: bool = False
    #: When a person last wrote, a notification landed, or a run started or
    #: finished here; the history list is ordered by it.
    last_activity_at: datetime
    last_run_status: AgentRunStatus | None = None
    last_run_error: str | None = None
    last_run_error_code: str | None = None
    last_run_error_reason: str | None = None
    last_run_finished_at: datetime | None = None
    last_run_retryable: bool = False
    has_pending_interaction: bool = Field(
        default=False,
        description=(
            "A question, approval or Agent Host permission request here is "
            "waiting on the conversation's owner. Not implied by status: a "
            "WAITING conversation may be waiting on a timer, and a permission "
            "request waits inside a RUNNING one."
        ),
    )
    waiting_on_work: bool = Field(
        default=False,
        description=(
            "WAITING on the agent's own work -- a sub-agent, a process or a "
            "timer -- rather than on a person: a run will start when it ends, "
            "so a viewer can follow the conversation to it. Set when one "
            "conversation is read, false in lists."
        ),
    )
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    #: Resolved from the conversation's own row when built from one; a response
    #: built any other way falls back to the directory a row with none records.
    _workspace_cwd: str | None = PrivateAttr(default=None)

    @model_validator(mode="wrap")
    @classmethod
    def _from_conversation(
        cls, data: object, handler: ModelWrapValidatorHandler[ConversationResponse]
    ) -> ConversationResponse:
        """A conversation as a client reads it: its typed state said in
        ``metadata`` too (``domain/wire_metadata``), and its directory."""
        if not isinstance(data, Conversation):
            return handler(data)
        response = handler(
            {
                **{
                    name: getattr(data, name)
                    for name in cls.model_fields
                    if name != "metadata" and hasattr(data, name)
                },
                "metadata": conversation_wire_metadata(data),
            }
        )
        response._workspace_cwd = resolve_workspace_location(data).cwd
        return response

    @computed_field(  # type: ignore[prop-decorator]
        return_type=str,
        description=(
            "The conversation's working directory in the sandbox. This is "
            "where the agent's shell starts and where its files land, so it is "
            "the directory a file pane should be showing."
        ),
    )
    @property
    def workspace_cwd(self) -> str:
        # The sibling of `pod_cwd`, and added for the same reason one filesystem
        # over. The file pane rebuilt this path itself as
        # `/workspace/conversations/{id}` -- which is the *fallback* branch of
        # `ToolContext.get_workspace_cwd()`, reached only by contexts with
        # no conversation row. Every real run resolves through
        # `workspace_location` to `/workspace/c/{date}/{slug}`, so the pane
        # asked for a directory that has never existed and got back an empty
        # listing, because a missing directory and an empty one answer
        # identically.
        return self._workspace_cwd or default_cwd_for(
            conversation_id=self.id, created_at=self.created_at
        )

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
        # Derived rather than stored: `workspace_location` owns the rule that
        # resolves the cwd. A client that rebuilt this path itself would be a
        # second implementation of a rule the agent's tools also depend on,
        # which is how an upload ends up somewhere the agent never looks.
        return pod_cwd_from_workspace_cwd(self.workspace_cwd)


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    limit: int
    next_page_token: str | None = None


class AgentRunStartResponse(BaseModel):
    conversation_id: UUID
    agent_run_id: UUID
    started_new_run: bool
    deduplicated: bool = Field(
        default=False,
        description=(
            "True when this send was already received -- the same "
            "client_message_id, or the same text from the same person a moment "
            "ago. Nothing new was stored; agent_run_id is the run already "
            "answering it."
        ),
    )

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

    @model_validator(mode="before")
    @classmethod
    def _from_message(cls, data: object) -> object:
        """A message as a client reads it: its typed state said in ``metadata``
        too (``domain/wire_metadata``)."""
        if not isinstance(data, Message):
            return data
        return {
            **{
                name: getattr(data, name)
                for name in cls.model_fields
                if name != "metadata" and hasattr(data, name)
            },
            "metadata": message_wire_metadata(data),
        }


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    limit: int
    next_page_token: str | None = None


class UserApprovalListResponse(BaseModel):
    items: list[MessageResponse]


class PendingInteractionResponse(BaseModel):
    """One pause waiting on the caller, and the conversation it is in."""

    conversation_id: UUID
    conversation_title: str | None = None
    conversation_status: ConversationStatus | None = None
    agent_id: UUID | None = Field(
        default=None,
        description="Absent for the pod's own assistant on older conversations.",
    )
    agent_name: str | None = None
    tool_call_id: str = Field(
        description=(
            "The paused call, which is also the approval id the decision "
            "endpoint takes."
        )
    )
    kind: PendingInteractionKind
    summary: str | None = Field(
        default=None,
        description="The question asked, or the approval's title, on one line.",
    )
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingInteractionListResponse(BaseModel):
    items: list[PendingInteractionResponse]
    limit: int
    next_page_token: str | None = None


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
    # Bounded like an agent's instruction: an empty message started a paid run
    # with nothing to answer, and an unbounded one is stored and replayed into
    # every later turn of the conversation.
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARACTERS)
    metadata: JsonObject | None = None
    client_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description=(
            "The sender's own id for this send, generated once per message the "
            "person sends and reused on every retry of it. A second send with an "
            "id already used in this conversation stores nothing and returns the "
            "first send's run, with deduplicated=true."
        ),
    )

    @field_validator("client_message_id")
    @classmethod
    def _not_a_system_name(cls, value: str | None) -> str | None:
        """The pod names some sends itself -- the turn a channel line starts,
        the news that answers arrived -- and a client never takes one of
        those names first."""
        if value is not None and value.startswith(SYSTEM_CLIENT_ID_PREFIXES):
            raise ValueError("That client_message_id is reserved")
        return value


#: The ``client_message_id`` names the pod gives its own sends in a
#: conversation: ``channel_answers.turn_client_id``,
#: ``message_reply_service.replies_client_id``.
SYSTEM_CLIENT_ID_PREFIXES = ("channel-line:", "replies:")


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    instruction: str = Field(min_length=1, max_length=MAX_AGENT_INSTRUCTION_CHARACTERS)
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
    instruction: str | None = Field(
        default=None, min_length=1, max_length=MAX_AGENT_INSTRUCTION_CHARACTERS
    )
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


# Runtime-profile schemas live in their own module; re-exported for the
# controllers and tests that import them from here.
from app.modules.agent.api.runtime_profile_schemas import (  # noqa: E402
    AgentRuntimeProfileDetailResponse as AgentRuntimeProfileDetailResponse,
    AgentRuntimeProfileListResponse as AgentRuntimeProfileListResponse,
    AgentRuntimeProfileResponse as AgentRuntimeProfileResponse,
    AgentRuntimeProfileTestResponse as AgentRuntimeProfileTestResponse,
    CreateAgentHostRuntimeProfileRequest as CreateAgentHostRuntimeProfileRequest,
    CreateAgentRuntimeProfileRequest as CreateAgentRuntimeProfileRequest,
    CreateAnthropicCompatibleRuntimeProfileRequest as CreateAnthropicCompatibleRuntimeProfileRequest,
    CreateOpenAICompatibleRuntimeProfileRequest as CreateOpenAICompatibleRuntimeProfileRequest,
    SetOrganizationDefaultRuntimeRequest as SetOrganizationDefaultRuntimeRequest,
    UpdateAgentHostRuntimeProfileRequest as UpdateAgentHostRuntimeProfileRequest,
    UpdateAgentRuntimeProfileRequest as UpdateAgentRuntimeProfileRequest,
    UpdateAnthropicCompatibleRuntimeProfileRequest as UpdateAnthropicCompatibleRuntimeProfileRequest,
    UpdateOpenAICompatibleRuntimeProfileRequest as UpdateOpenAICompatibleRuntimeProfileRequest,
)

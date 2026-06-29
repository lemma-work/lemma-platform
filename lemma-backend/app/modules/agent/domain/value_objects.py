"""Value objects for the unified agent module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

JsonPrimitive = str | int | float | bool | None
JsonValue = object
JsonObject = dict[str, object]


class HarnessKind(str, Enum):
    """Runtime framework used to execute an agent."""

    LEMMA = "LEMMA"
    CODEX = "CODEX"
    CLAUDE_CODE = "CLAUDE_CODE"
    OPENCODE = "OPENCODE"
    CURSOR = "CURSOR"
    ANTIGRAVITY = "ANTIGRAVITY"

    @classmethod
    def _missing_(cls, value: object) -> "HarnessKind | None":
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        aliases = {
            "pydantic_ai": cls.LEMMA,
            "PYDANTIC_AI": cls.LEMMA,
            "lemma": cls.LEMMA,
            "codex": cls.CODEX,
            "claude_code": cls.CLAUDE_CODE,
            "opencode": cls.OPENCODE,
            "cursor": cls.CURSOR,
            "cursor_agent": cls.CURSOR,
            "antigravity": cls.ANTIGRAVITY,
            "agy": cls.ANTIGRAVITY,
        }
        if normalized in aliases:
            return aliases[normalized]
        upper = normalized.upper()
        if upper in aliases:
            return aliases[upper]
        for member in cls:
            if member.value == normalized or member.value.upper() == upper:
                return member
        return None


class AgentToolset(str, Enum):
    """Known tool bundles an agent may request."""

    WORKSPACE_CLI = "WORKSPACE_CLI"
    SKILLS = "SKILLS"
    WEB_SEARCH = "WEB_SEARCH"
    USER_INTERACTION = "USER_INTERACTION"
    SPEECH = "SPEECH"
    POD = "POD"
    SUBAGENTS = "SUBAGENTS"
    TODO = "TODO"


class ConnectorMode(str, Enum):
    """Connector account ownership mode for access configuration."""

    AGENT_OWNED = "AGENT_OWNED"
    USER_OWNED = "USER_OWNED"

    @classmethod
    def normalize(cls, value: object) -> "ConnectorMode":
        if isinstance(value, cls):
            return value
        normalized = str(value).upper()
        legacy_modes = {
            "FIXED": cls.AGENT_OWNED,
            "DYNAMIC": cls.USER_OWNED,
        }
        if normalized in legacy_modes:
            return legacy_modes[normalized]
        return cls(normalized)


class TableAccessMode(str, Enum):
    """Mode for workload table access permissions."""

    READ = "READ"
    WRITE = "WRITE"


class TableAccessEntry(BaseModel):
    """Per-table access configuration for workloads."""

    table_name: str
    mode: TableAccessMode = TableAccessMode.WRITE

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        table_name = value.strip()
        if not table_name:
            raise ValueError("table_name cannot be empty")
        return table_name


class ConnectorAccessConfig(BaseModel):
    """Configuration for connector access granted to a workload."""

    app_name: str = Field(..., description="Name of the connector")
    mode: ConnectorMode = Field(..., description="Connector account ownership mode")
    account_id: UUID | None = Field(
        None,
        description="Required for AGENT_OWNED mode - specific account to use",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value: object) -> ConnectorMode:
        return ConnectorMode.normalize(value)

    def __init__(self, **data):
        super().__init__(**data)
        if self.mode == ConnectorMode.AGENT_OWNED and not self.account_id:
            raise ValueError("AGENT_OWNED mode requires account_id to be specified")

    def to_dict(self) -> dict[str, str]:
        result = {
            "app_name": self.app_name,
            "mode": self.mode.value,
        }
        if self.account_id:
            result["account_id"] = str(self.account_id)
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ConnectorAccessConfig":
        account_id = data.get("account_id")
        if account_id:
            account_id = UUID(account_id) if isinstance(account_id, str) else account_id
        return cls(
            app_name=data["app_name"],
            mode=data["mode"],
            account_id=account_id,
        )


class AgentRunStatus(str, Enum):
    """Internal lifecycle for one harness execution."""

    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    @classmethod
    def _missing_(cls, value: object) -> "AgentRunStatus | None":
        if not isinstance(value, str):
            return None
        normalized = value.upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class ConversationStatus(str, Enum):
    """User-visible lifecycle for a conversation."""

    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"

    @classmethod
    def _missing_(cls, value: object) -> "ConversationStatus | None":
        if not isinstance(value, str):
            return None
        normalized = value.upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class AgentRunApprovalDecision(str, Enum):
    """User decision scope for a run approval."""

    APPROVE_ONCE = "APPROVE_ONCE"
    APPROVE_FOR_SESSION = "APPROVE_FOR_SESSION"
    DENY = "DENY"

    @classmethod
    def _missing_(cls, value: object) -> "AgentRunApprovalDecision | None":
        if not isinstance(value, str):
            return None
        normalized = value.upper()
        aliases = {
            "APPROVE": cls.APPROVE_ONCE,
            "ALLOW": cls.APPROVE_ONCE,
            "ACCEPT": cls.APPROVE_ONCE,
            "ONCE": cls.APPROVE_ONCE,
            "SESSION": cls.APPROVE_FOR_SESSION,
            "APPROVE_SESSION": cls.APPROVE_FOR_SESSION,
            "DENIED": cls.DENY,
            "DECLINE": cls.DENY,
        }
        if normalized in aliases:
            return aliases[normalized]
        for member in cls:
            if member.value == normalized:
                return member
        return None


@dataclass(slots=True)
class AgentRunFinishResult:
    """Result of attempting to move an agent run into a terminal state."""

    status: AgentRunStatus
    conversation_status: ConversationStatus
    updated: bool


ACTIVE_AGENT_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.RUNNING,
        AgentRunStatus.STOP_REQUESTED,
    }
)

TERMINAL_AGENT_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.STOPPED,
    }
)

DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT = 100_000
DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES = 20


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageKind(str, Enum):
    """Discriminates the flat message body.

    A message carries exactly one kind. Textual kinds use ``text``; tool kinds
    use ``tool_name``/``tool_call_id`` plus ``tool_args`` (call) or
    ``tool_result`` (return). There is no nested ``content`` object.
    """

    TEXT = "TEXT"
    NOTIFICATION = "NOTIFICATION"
    THINKING = "THINKING"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RETURN = "TOOL_RETURN"

    @classmethod
    def _missing_(cls, value: object) -> "MessageKind | None":
        # Case-insensitive so a lowercase kind from an older daemon payload or
        # a pre-migration row still resolves to the CAPS member.
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


TEXTUAL_MESSAGE_KINDS = frozenset(
    {MessageKind.TEXT, MessageKind.NOTIFICATION, MessageKind.THINKING}
)


class MessageDraft(BaseModel):
    """Harness-produced message before durable DB id/sequence assignment.

    Flat by construction: the body lives directly on the draft instead of a
    nested ``content`` union, so there is no ``content.content`` indirection.
    Build via the ``of_*`` constructors to keep call sites readable.
    """

    role: MessageRole
    kind: MessageKind
    text: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_args: JsonValue = None
    tool_result: JsonValue = None
    metadata: JsonObject | None = None

    @classmethod
    def of_text(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(role=role, kind=MessageKind.TEXT, text=text, metadata=metadata)

    @classmethod
    def of_thinking(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(role=role, kind=MessageKind.THINKING, text=text, metadata=metadata)

    @classmethod
    def of_notification(
        cls,
        text: str,
        *,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role, kind=MessageKind.NOTIFICATION, text=text, metadata=metadata
        )

    @classmethod
    def of_tool_call(
        cls,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_args: JsonValue = None,
        role: MessageRole = MessageRole.ASSISTANT,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role,
            kind=MessageKind.TOOL_CALL,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_args=tool_args,
            metadata=metadata,
        )

    @classmethod
    def of_tool_return(
        cls,
        *,
        tool_call_id: str,
        tool_name: str | None = None,
        tool_result: JsonValue = None,
        role: MessageRole = MessageRole.TOOL,
        metadata: JsonObject | None = None,
    ) -> "MessageDraft":
        return cls(
            role=role,
            kind=MessageKind.TOOL_RETURN,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_result=tool_result,
            metadata=metadata,
        )


class AgentEventType(str, Enum):
    """Event type emitted by a harness while running an agent.

    Internal only: the SSE wire ``type`` strings are hardcoded in
    ``services/realtime.py`` and the daemon protocol is parsed case-insensitively
    via ``_missing_`` below, so these values are never compared raw on the wire.
    """

    TOKEN = "TOKEN"
    MESSAGE = "MESSAGE"
    STATUS = "STATUS"
    USAGE = "USAGE"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    # The run paused waiting for the user (ask_user / request_approval). Terminal
    # for this run; the user's submission starts a fresh run that resumes.
    WAITING = "WAITING"

    @classmethod
    def _missing_(cls, value: object) -> "AgentEventType | None":
        # The daemon hub builds events from wire payloads that historically used
        # lowercase type strings ("completed", "message", ...); accept any case.
        if not isinstance(value, str):
            return None
        normalized = value.upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class ConversationType(str, Enum):
    """User-visible conversation behavior."""

    CHAT = "CHAT"
    TASK = "TASK"
    # A pinned conversation that acts as a project/group: it owns a workspace cwd
    # and conversations created with parent_id pointing at it inherit that cwd
    # (see ConversationService.create_conversation). Usually empty (no runs).
    PROJECT = "PROJECT"


class AgentRunUsage(BaseModel):
    """Normalized billable usage produced during an agent run."""

    model_name: str
    usage_kind: str = "llm"
    input_tokens: int = 0
    output_tokens: int = 0
    units: float = 0.0
    request_count: int = 0
    tool_call_count: int = 0
    metadata: JsonObject | None = None


class AgentEvent(BaseModel):
    """Normalized event emitted by an agent harness or conversation service."""

    type: AgentEventType
    data: object
    agent_run_id: UUID | None = None
    sequence: int | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


@dataclass(slots=True)
class HarnessOptions:
    """Dependency-injected options for one harness execution."""

    model_name: str
    toolsets: list[object] = field(default_factory=list)
    # Pydantic AI capabilities (current-time, prompt-caching, tool-search, todo,
    # deferred extra-tools-over-MCP). Built only for the in-process LEMMA harness;
    # ignored by daemon harnesses (Codex/Claude-Code), which use the MCP server.
    capabilities: list[object] = field(default_factory=list)
    usage_limits: object | None = None
    output_type: object | None = None
    model_settings: JsonObject | None = None
    history_processors: list[object] = field(default_factory=list)
    history_summarization_enabled: bool = True
    history_summarization_token_limit: int = DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT
    history_summarization_keep_messages: int = (
        DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES
    )
    should_stop: Callable[[], Awaitable[bool]] | None = None
    extra: JsonObject = field(default_factory=dict)


class AgentRuntimeConfig(BaseModel):
    """Agent runtime selector using a profile plus optional catalog model."""

    profile_id: str = Field(min_length=1)
    model_name: str | None = Field(default=None, min_length=1)

    @field_validator("profile_id")
    @classmethod
    def normalize_profile_id(cls, value: str) -> str:
        profile_id = value.strip()
        if not profile_id:
            raise ValueError("profile_id cannot be empty")
        return profile_id

    @field_validator("model_name")
    @classmethod
    def normalize_model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        model_name = value.strip()
        if not model_name:
            raise ValueError("model_name cannot be empty")
        return model_name

    @model_serializer(mode="wrap")
    def serialize_without_unset_model_name(self, handler):
        data = handler(self)
        if data.get("model_name") is None:
            data.pop("model_name", None)
        return data


class AgentRunStartResult(BaseModel):
    """Result returned after adding a user message to a conversation."""

    conversation_id: UUID
    agent_run_id: UUID
    started_new_run: bool


def to_json_value(value: object) -> JsonValue:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [to_json_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


__all__ = [
    "ACTIVE_AGENT_RUN_STATUSES",
    "ConnectorAccessConfig",
    "ConnectorMode",
    "AgentEvent",
    "AgentEventType",
    "AgentRuntimeConfig",
    "AgentRunFinishResult",
    "AgentRunStartResult",
    "AgentRunStatus",
    "AgentRunApprovalDecision",
    "ConversationStatus",
    "ConversationType",
    "AgentToolset",
    "DEFAULT_HISTORY_SUMMARIZATION_KEEP_MESSAGES",
    "DEFAULT_HISTORY_SUMMARIZATION_TOKEN_LIMIT",
    "HarnessKind",
    "HarnessOptions",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "MessageDraft",
    "MessageKind",
    "MessageRole",
    "TableAccessEntry",
    "TableAccessMode",
    "TEXTUAL_MESSAGE_KINDS",
    "TERMINAL_AGENT_RUN_STATUSES",
    "to_json_value",
]

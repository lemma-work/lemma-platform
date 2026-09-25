"""The frames of the Agent Host link: one WebSocket per paired host.

See docs/architecture/agent-host.md#the-link. The same vocabulary is defined a
second time in ``desktop/agent-host/src/link/protocol.rs``, and both copies are
held to ``desktop/agent-host/tests/fixtures/wire_contract.json`` by
``test_agent_host_wire_contract.py``. A frame one side sends and the other does
not know is a request that is never answered, so nothing here is renamed without
changing the fixture in the same commit.

Every frame is ``{type, id?, re?, body}``. ``id`` names a request that expects
an answer and ``re`` on the answer points back at it; pushes carry neither.

The bodies reuse the domain models the HTTP routes already took, on purpose:
the link moved the transport and kept every rule. What is new is only what a
socket needs and a request/response pair did not -- somewhere to say which
update was refused, and a vocabulary of close codes.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.agent_host import (
    AGENT_HOST_PROTOCOL_VERSION,
    AgentHostCapacity,
    AgentHostCommand,
    AgentHostEventAck,
    AgentHostHarnessSnapshot,
    AgentHostPairingComplete,
    HostHello,
)
from app.modules.agent.domain.value_objects import JsonObject


#: The router path. It sits beside the rest of ``/agent-host/*``, which the
#: global session gate already exempts: the host authenticates with its own
#: secret on the first frame, not with a user session.
AGENT_HOST_LINK_PATH = "/agent-host/link"

#: The longest the host goes without a frame. ``control`` is the heartbeat, and
#: it also carries the checkpoint that renews each run's 90-second lease, so 20
#: seconds renews a lease four times over and keeps an idle socket well inside
#: Cloudflare's 100-second limit.
AGENT_HOST_LINK_HEARTBEAT_MS = 20_000

#: Silence for this many heartbeats closes the socket with ``HEARTBEAT_TIMEOUT``.
AGENT_HOST_LINK_MISSED_HEARTBEATS = 3

#: How long a caller waits for some replica's link to take an ``op`` before it
#: treats the host as offline. See docs/architecture/desktop-host-execution.md.
OP_PICKUP_TIMEOUT_SECONDS = 2.0

#: The most binary data one ``op`` frame carries, before base64.
OP_MAX_DATA_BYTES = 1024 * 1024

#: The longest deadline an ``op`` may carry: a ``process.read`` waits at most
#: 35 seconds and a large write chunk is seconds at worst.
OP_MAX_DEADLINE_MS = 10 * 60 * 1000

#: ``op`` server ids start with this, so they cannot collide with the ids the
#: host puts on its own requests.
OP_ID_PREFIX = "s"


class OpMethod:
    """The ``op`` methods, as the exec-server names them."""

    WORKSPACE_OPEN = "workspace.open"
    WORKSPACE_CLOSE = "workspace.close"
    PROCESS_START = "process.start"
    PROCESS_READ = "process.read"
    PROCESS_INPUT = "process.input"
    PROCESS_RESIZE = "process.resize"
    PROCESS_TERMINATE = "process.terminate"
    PROCESS_LIST = "process.list"
    FILE_STAT = "file.stat"
    FILE_LIST = "file.list"
    FILE_MKDIR = "file.mkdir"
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    FILE_MOVE = "file.move"
    FILE_DELETE = "file.delete"
    SECRET_DELIVER = "secret.deliver"


#: Every ``detail.kind`` an ``OP_FAILED`` error may carry.
OP_FAILURE_KINDS = frozenset(
    {
        "not_found",
        "already_exists",
        "not_a_directory",
        "is_a_directory",
        "permission_denied",
        "outside_workspace",
        "digest_mismatch",
        "too_large",
        "process_not_found",
        "workspace_not_open",
        "exec_server_unavailable",
        "timeout",
        "invalid_request",
        "io_error",
    }
)

#: The only kinds the host marks ``retryable``.
OP_RETRYABLE_KINDS = frozenset({"exec_server_unavailable", "timeout"})


class LinkCloseCode:
    """Why the socket closed, as numbers the host branches on.

    Plain integers rather than an Enum: they are handed straight to
    ``websocket.close(code=...)`` and compared against the fixture's numbers.
    """

    NORMAL = 1000
    RESTARTING = 1012
    PROTOCOL_VIOLATION = 4400
    REVOKED_OR_MISSING = 4401
    INVALID_CREDENTIAL = 4403
    HEARTBEAT_TIMEOUT = 4408
    SUPERSEDED = 4409
    UPGRADE_REQUIRED = 4426


#: The fixture's names for the codes above, which is how the contract test
#: compares them without a second hand-written table.
LINK_CLOSE_CODES: dict[str, int] = {
    "normal": LinkCloseCode.NORMAL,
    "restarting": LinkCloseCode.RESTARTING,
    "protocol_violation": LinkCloseCode.PROTOCOL_VIOLATION,
    "revoked_or_missing": LinkCloseCode.REVOKED_OR_MISSING,
    "invalid_credential": LinkCloseCode.INVALID_CREDENTIAL,
    "heartbeat_timeout": LinkCloseCode.HEARTBEAT_TIMEOUT,
    "superseded": LinkCloseCode.SUPERSEDED,
    "upgrade_required": LinkCloseCode.UPGRADE_REQUIRED,
}

#: The close reason for an unknown or revoked secret. Deliberately identical for
#: both, exactly as the HTTP 401 was: telling them apart would let anyone holding
#: a guessed secret learn whether it was ever real.
REVOKED_OR_MISSING_REASON = "AGENT_HOST_REVOKED_OR_MISSING"


class LinkErrorCode(str, Enum):
    """What an ``error`` frame says went wrong with one request.

    The event codes are the distinctions the host acts on. A ``NOT_FOUND`` or
    ``STALE_LEASE`` batch belongs to a run this host no longer owns, so its
    outbox for that run can go; a ``SEQUENCE_GAP`` means the host must resend
    from what Lemma last acknowledged; ``TERMINAL_RUN`` means the run ended
    first. One HTTP status could not carry those apart, which is why the host
    used to read the message text.
    """

    INVALID_FRAME = "INVALID_FRAME"
    NOT_FOUND = "NOT_FOUND"
    STALE_LEASE = "STALE_LEASE"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    TERMINAL_RUN = "TERMINAL_RUN"
    UNAUTHORIZED = "UNAUTHORIZED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"
    #: An ``op`` the host could not perform. ``detail.kind`` says why; see
    #: ``OP_FAILURE_KINDS``.
    OP_FAILED = "OP_FAILED"


class HostFrameType(str, Enum):
    PAIR = "pair"
    HELLO = "hello"
    CONTROL = "control"
    EVENTS = "events"
    HARNESSES = "harnesses"
    MCP = "mcp"
    INTERACTION_WAIT = "interaction_wait"
    REVOKE = "revoke"
    OP_OK = "op_ok"
    ERROR = "error"


class ServerFrameType(str, Enum):
    PAIRED = "paired"
    WELCOME = "welcome"
    CONTROL_OK = "control_ok"
    EVENTS_OK = "events_ok"
    HARNESSES_OK = "harnesses_ok"
    MCP_OK = "mcp_ok"
    INTERACTION_OK = "interaction_ok"
    REVOKED = "revoked"
    COMMANDS = "commands"
    RECONNECT = "reconnect"
    OP = "op"
    ERROR = "error"


class LinkFrame(BaseModel):
    """The envelope every frame travels in, in both directions.

    ``type`` stays a plain string here so an unknown type is a protocol answer
    the session chooses (an ``error`` naming it) rather than a validation error
    that loses the ``id`` the answer has to carry.
    """

    type: str = Field(min_length=1, max_length=64)
    id: str | None = Field(default=None, min_length=1, max_length=64)
    re: str | None = Field(default=None, min_length=1, max_length=64)
    body: JsonObject = Field(default_factory=dict)


# ---------------------------------------------------------------- host frames


#: Consume a pairing code; the answer (``paired``) carries the secret, shown
#: exactly once. The same body the HTTP pairing route took.
PairBody = AgentHostPairingComplete


class HostExecutionCapability(BaseModel):
    """Whether this host runs its owner's Lemma commands on the machine itself.

    See docs/architecture/desktop-host-execution.md. ``enabled`` is the owner's
    toggle (Settings, This Mac, Coding agents); ``available`` is whether the
    exec-server could actually be started under its sandbox profile right now.
    Both have to hold before a run is sent here. The host sends it as its own
    field on ``hello`` and on every ``control``; Lemma keeps it under
    ``host_execution`` in the host row's ``capacity``.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    platform: str | None = Field(default=None, max_length=32)
    available: bool = False

    @property
    def usable(self) -> bool:
        return self.enabled and self.available


class HelloBody(BaseModel):
    hello: HostHello
    capacity: AgentHostCapacity = Field(default_factory=AgentHostCapacity)
    #: Absent from a host too old to know it, which means "no host execution".
    host_execution: HostExecutionCapability | None = None


class ControlBody(BaseModel):
    """The heartbeat, carrying whatever the host has to report.

    The three lists are left raw on purpose and parsed one item at a time by the
    session. The poll parsed them as a whole, so one malformed checkpoint 422'd
    the request that carried every other update -- and the only commands that
    host could receive. Here a malformed item is named in ``control_ok.refused``
    and the rest are applied.
    """

    capacity: AgentHostCapacity = Field(default_factory=AgentHostCapacity)
    #: On every heartbeat, so turning host execution on or off applies within
    #: one instead of at the next reconnect. Absent means unchanged.
    host_execution: HostExecutionCapability | None = None
    acknowledged_command_ids: list[object] = Field(default_factory=list, max_length=256)
    checkpoints: list[object] = Field(default_factory=list, max_length=256)
    rejections: list[object] = Field(default_factory=list, max_length=256)


class HarnessesBody(BaseModel):
    harnesses: list[AgentHostHarnessSnapshot] = Field(min_length=1, max_length=32)


# The tool call id a parked interaction is addressed by. The same bound the
# HTTP route matched in its path.
_TOOL_CALL_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,200}$"


class McpBody(BaseModel):
    """One Lemma MCP request from the agent, relayed by the host's bridge.

    ``token`` is the run's own Lemma credential and is re-authorized on every
    call against ``conversation_id``, exactly as the HTTP mount did. The host
    being authenticated says which machine is talking, not which conversation
    it may act in.
    """

    run_id: UUID | None = None
    conversation_id: UUID
    token: str = Field(min_length=1, max_length=8192)
    method: Literal["tools/list", "tools/call"]
    params: JsonObject = Field(default_factory=dict)


class InteractionWaitBody(BaseModel):
    """Wait for a person to decide a parked ``ask_user`` / ``request_approval``."""

    run_id: UUID | None = None
    conversation_id: UUID
    token: str = Field(min_length=1, max_length=8192)
    tool_call_id: str = Field(pattern=_TOOL_CALL_ID_PATTERN)


class ErrorBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=4096)
    retryable: bool = False
    #: Carried by an answer to an ``op``: ``{"kind": ...}``.
    detail: JsonObject | None = None


class OpOkBody(BaseModel):
    """The host's answer to one ``op``. ``result`` is the method's own shape."""

    result: JsonObject = Field(default_factory=dict)


# -------------------------------------------------------------- server frames


class WelcomeBody(BaseModel):
    host_id: UUID
    user_id: UUID
    protocol_version: int = AGENT_HOST_PROTOCOL_VERSION
    heartbeat_ms: int = AGENT_HOST_LINK_HEARTBEAT_MS


class RefusedUpdate(BaseModel):
    """A control update Lemma could not parse, and so will never apply.

    The only way an update is refused. A stale or inapplicable one is a no-op
    instead, because the host clears its outbox only on acceptance and would
    otherwise resend it for ever. ``index`` is its position in the list it came
    in; the ids are echoed as strings because an unparseable item may not have
    a valid UUID to echo.
    """

    kind: Literal["ack", "checkpoint", "rejection"]
    index: int = Field(ge=0)
    command_id: str | None = None
    run_id: str | None = None
    reason: str


class ControlOkBody(BaseModel):
    commands: list[AgentHostCommand] = Field(default_factory=list)
    refused: list[RefusedUpdate] = Field(default_factory=list)


class CommandsBody(BaseModel):
    commands: list[AgentHostCommand] = Field(min_length=1)


class EventsOkBody(BaseModel):
    ack: AgentHostEventAck


class AgentHostHarnessRecord(BaseModel):
    """A harness as Lemma stored it: the snapshot plus the ids it was given."""

    id: UUID
    host_id: UUID
    harness_key: str
    display_name: str
    adapter_version: str
    upstream_version: str | None
    health: str
    capabilities: dict[str, object]
    config_revision: str
    config_options: list[object]
    stale_after: datetime
    stale_reason: str | None

    model_config = ConfigDict(from_attributes=True)


class HarnessesOkBody(BaseModel):
    items: list[AgentHostHarnessRecord]


class McpOkBody(BaseModel):
    """``result`` is an MCP ``ListToolsResult`` or ``CallToolResult``, as JSON."""

    result: JsonObject


class InteractionOkBody(BaseModel):
    answer: JsonObject


class OpBody(BaseModel):
    """One operation inside a host workspace, sent from Lemma to the host.

    ``workspace`` is the sandbox's logical id; the host maps it to the root it
    was given at ``workspace.open`` and refuses anything else.
    """

    workspace: UUID
    method: str = Field(min_length=1, max_length=64)
    params: JsonObject = Field(default_factory=dict)
    deadline_ms: int = Field(ge=1, le=OP_MAX_DEADLINE_MS)


class ReconnectBody(BaseModel):
    after_ms: int = Field(ge=0, le=60_000)


class ServerErrorBody(BaseModel):
    code: LinkErrorCode
    message: str
    retryable: bool = False

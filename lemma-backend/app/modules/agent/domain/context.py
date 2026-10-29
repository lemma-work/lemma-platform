"""The one context a run's tools execute against: ``ToolContext``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.modules.agent.domain.outsiders import Audience, refuse_owner_workspace
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.domain.workspace_location import pod_cwd_from_workspace_cwd
from app.modules.workspace.contracts.host_execution import HostWorkspace

if TYPE_CHECKING:
    from app.modules.workspace.contracts.tooling import WorkspaceFileManager


@runtime_checkable
class ToolCaller(Protocol):
    """Runs another of the run's tools by name: the approval re-run's way in,
    and ``call_tool``'s."""

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object] | None,
        ctx: ToolContext,
        *,
        tool_call_id: str | None = None,
    ) -> object: ...


class ApprovedExecution(BaseModel):
    """One tool call a person approved, and exactly what the approval lends it.

    Approval is how an agent does what its own grants do not allow: a missing
    grant, a destructive action, an auth-required step all come back as
    ``needs_approval``, and the person the agent works for approves exactly one
    described call. It is never standing access -- the agent's next call is
    judged on its own grants again.

    The call still runs as the agent: the same delegated context it would have
    had, plus ``permission_ids`` -- the permissions the server refused this
    exact call for -- answered as approved. It used to run as the approver's
    whole user context, so approving "delete this row" handed the agent
    everything the person could do for the length of the call: an approved
    subagent spawn, connector call or sign-in ran with all of it (H7). The
    person's ceiling applies either way; an approver lends only what they hold.

    Carried on the tool context of the approved call only, so every tool builds
    its authorization context from it explicitly (``tool_authorization_context``)
    instead of inferring "this is the user" from a missing workload id, which is
    what #597 broke.
    """

    model_config = ConfigDict(frozen=True)

    #: The person lending their authority: always the conversation's owner, the
    #: user the agent is working for.
    approver_user_id: UUID
    #: The agent that asked. Recorded for the audit trail; it grants nothing.
    agent_id: UUID | None
    conversation_id: UUID
    tool_name: str
    #: The ``request_approval`` call id, or ``None`` when an earlier session
    #: approval covered this exact call.
    approval_id: str | None = None
    #: What the approval lends: what its card showed -- the refusals the
    #: server had recorded when the card went up (``tools/approval_denials``).
    #: Never the ``permission_ids`` the model put in its ``request_approval``
    #: -- those are whatever it wrote -- nor a refusal recorded since. Empty
    #: when nothing was pending: the call then runs with exactly the agent's
    #: own authority.
    permission_ids: frozenset[str] = frozenset()


class ToolContext(BaseModel):
    """Who a run acts for and where, as every tool and harness reads it.

    One type: built by ``RunSession`` for a turn, a resumed approval and a
    relayed Agent Host call, and pod-scoped for the pod MCP server.
    """

    user_id: UUID
    org_id: UUID | None = None
    pod_id: UUID
    conversation_id: UUID
    agent_name: str | None = None
    agent_run_id: UUID | None = None
    # Whether this run is the pod's own assistant rather than a named agent.
    # Every builder of this context resolves it from the agent it already holds
    # (`AgentKind.POD_DEFAULT`, or `is_pod_default_agent` where only ids are in
    # hand), so nothing downstream has to re-derive it from an id or a name.
    #
    # Two things read it. The LEMMA capability assembler keeps POD/SUBAGENTS
    # deferred behind ToolSearch for the assistant, because it accumulates every
    # optional toolset, while a named agent chose its own and gets them injected
    # directly. And the tool-side authorization contexts delegate as the
    # invoking user for the assistant, where a named agent is limited to its own
    # resource grants.
    is_pod_default_agent: bool = False
    #: The tool surface this run's pod assistant gets (``domain/tool_profile``),
    #: a ``ToolProfile`` value; "today" for every other run.
    tool_profile: str = "today"
    # Set only on the context of a call a person approved; see
    # `ApprovedExecution`. Tools must not read it directly: they get their
    # authorization context from `tool_authorization_context`.
    approved_execution: ApprovedExecution | None = None
    # Whether this run gets the memory contract and its AGENTS.md scopes. Not
    # simply "MEMORY is on the agent": memory carries no tools, so it is inert
    # without WORKSPACE_CLI or POD to read and write with -- see
    # `memory_is_active`, which the run-context builder resolves once here so
    # the capability assembler and the brief builder cannot disagree about it.
    memory_enabled: bool = False
    # Whether this run is told to write memory only when asked, its memory
    # being kept by a pass after it ends (`memory_kept_after_turn`). Resolved
    # with `memory_enabled`; the prompt reads it, and the run's terminal write
    # queues the pass from the same value, so the two cannot disagree.
    memory_after_turn: bool = False
    # This agent's grant summary, loaded once for the run. Toolset selection
    # derives POD and CONNECTORS from it, and the tool assembler reuses it
    # rather than reading the same rows a second time. Typed loosely here to
    # keep this domain model free of a tools-layer import.
    grant_summary: object | None = None
    # The toolsets this run actually has -- declared, always-on and
    # grant-derived -- resolved once by the run-context builder. The prompt
    # reads its toolset guidance from this, so it describes the tools the run
    # was given rather than the ones the agent row lists.
    run_toolsets: list[AgentToolset] | None = None
    # The part of the runtime brief that holds for the whole conversation (pod,
    # user, this agent, what it can reach), appended to the system prompt.
    # Built once per run by the runner; harness-neutral so it just rides along.
    context_brief: str | None = None
    # The text of the doc this conversation is attached to, rendered as a prompt
    # section; read fresh each run because the doc changes between turns.
    attached_document: str | None = None
    # What this person still owes somebody an answer to, rendered; None when
    # nothing is open. Its tools are offered only when it is set.
    open_questions: str | None = None
    # Everything that changes from one run of the conversation to the next,
    # rendered once per run (`domain/run_context_prompt`). Sent next to the
    # current turn and never stored, so the system prompt and the replayed
    # history stay byte-identical between runs.
    run_context: str | None = None
    # Whom this run answers (see `domain/outsiders`). On a run answering
    # somebody outside the pod, tools authorize as nobody -- Public reads only,
    # plus a contact's own rows -- rather than as `user_id`, which on such a run
    # is whoever looks after the conversation, not whoever asked. Resolved once
    # from the conversation by every builder of this context, so no tool has to
    # read the conversation to find out, and none can read it differently.
    audience: Audience = Audience()
    # False for a run started by a private note (see `domain/private_notes`):
    # its answer stays in Lemma instead of going to the chat platform the
    # conversation also lives on. Read by the surface's run observer.
    delivers_to_surface: bool = True
    # On a run answering outsiders: the member who looks after the conversation
    # started it, typing in Lemma (`domain/private_notes.keeper_started`). Still
    # a stranger's run in what it may reach, but nobody outside is asking, so
    # `channel_post` -- which only reaches that member -- has nothing to do.
    keeper_asking: bool = False

    workload_type: str | None = "agent"
    workload_id: UUID | None = None
    # The scope a delegated token carried (e.g. a function's
    # `{function.execute}`), applied on top of the workload's grants. Empty
    # means unscoped, as for every context Lemma builds for its own runs.
    delegation_scope: frozenset[str] = frozenset()
    configured_accounts: dict[str, UUID] = Field(default_factory=dict)
    surface_id: UUID | None = None
    surface_platform: str | None = None
    surface_metadata: object | None = None
    external_channel_id: str | None = None
    external_thread_id: str | None = None
    external_user_id: str | None = None
    external_message_id: str | None = None
    agent_display_name: str | None = None
    surface_conversation_kind: str | None = None
    #: The kind of place its surface records the conversation in, and whether a
    #: web widget's visitor is in it -- whoever asked this run, where the
    #: fields above are null for a run asked in Lemma. What a stranger's run is
    #: offered depends on them (``services/tool_catalog``).
    place_kind: str | None = None
    on_web_widget: bool = False
    runtime_profile: dict[str, object] | None = None
    # No provider credentials here: this object reaches every tool, and no tool
    # needs the model provider's key. The harness gets it through its options.
    workspace_id: str = "default"
    workspace_cwd: str | None = None
    # The GitHub repository this conversation works in, when it was started
    # against a project rather than a scratchpad; None means the scratchpad cwd.
    workspace_repo: ProjectRepo | None = None
    # Default pod-filesystem working directory for this conversation, e.g.
    # `/me/c/{date}/{slug}`. Relative pod tool paths resolve against this.
    pod_cwd: str | None = None
    # Set when this run's commands and files execute on the installation
    # user's Mac rather than in the VM (docs/architecture/
    # desktop-host-execution.md), chosen once when the context is built. The
    # browser stays in the VM whatever this says.
    host_workspace: HostWorkspace | None = None
    # An Agent Host run whose owner has host execution on: the agent already
    # has its own shell and file tools on the Mac, so Lemma's are withheld.
    host_runs_native_commands: bool = False
    # Whether this run has a tool that reads a file saved in the workspace
    # (`exec_command`). `web_fetch` hands page text back inline when not.
    workspace_readable: bool = True
    # How image-returning tools answer on this run, derived from the resolved
    # model. UNAVAILABLE is the safe default: image content sent to a
    # text-only model fails the whole request. See `domain/vision`.
    vision_mode: AgentVisionMode = AgentVisionMode.UNAVAILABLE
    # True only inside the in-process harness, which catches the
    # `AgentInputRequired` pause signal. Over MCP the run cannot be paused mid
    # tool call, so the pausing tools park instead.
    supports_pause_signal: bool = False
    # Set per call by the tool runtime: the call's durable id (an approval
    # card, a widget and a parked call are addressed by it), and the runtime
    # itself, through which an approved call re-runs a sibling tool.
    tool_call_id: str | None = None
    tools: ToolCaller | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def organization_id(self) -> UUID | None:
        """Compatibility alias used by workspace tooling."""
        return self.org_id

    @property
    def host_execution_mode(self) -> Literal["native", "sandbox"] | None:
        """How this run's commands reach the user's Mac, if they do.

        ``"native"``: an Agent Host run whose own tools are on the Mac.
        ``"sandbox"``: an in-process run whose ``exec_command`` runs there.
        """
        if self.host_runs_native_commands:
            return "native"
        if self.host_workspace is not None:
            return "sandbox"
        return None

    @property
    def file_manager(self) -> WorkspaceFileManager:
        """The VM workspace's files, even on a host-execution run: the manager
        reaches the VM, where the host root means nothing. Never for a run
        answering somebody outside the pod."""
        from app.modules.workspace.contracts.tooling import WorkspaceFileManager

        refuse_owner_workspace(self)
        return WorkspaceFileManager(
            self.user_id, cwd=self.workspace_cwd or WORKSPACE_ROOT
        )

    def get_workspace_cwd(self) -> str:
        """This conversation's directory, or the project root when there is none.

        The root, not a directory named after the conversation id: the pod MCP
        bridge serves a pod token with a nil conversation, and the project root
        is the honest answer for "no conversation".
        """
        if self.host_workspace is not None:
            return self.host_workspace.root
        return self.workspace_cwd or WORKSPACE_ROOT

    def get_pod_cwd(self) -> str:
        # The main run path always sets `pod_cwd`. The fallback mirrors the
        # workspace cwd, because the two are one short path under two roots.
        return self.pod_cwd or pod_cwd_from_workspace_cwd(self.get_workspace_cwd())

    def get_workspace_scope_key(self) -> str:
        return f"workspace:{self.workspace_id}:conversation:{self.conversation_id}"

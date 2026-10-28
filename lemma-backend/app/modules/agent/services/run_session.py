"""What one run works against, loaded once: ``RunSession``.

A run's tools see a context -- who is asking, which pod and workspace, which
agent with which grants, on which runtime, how its model reads images, where
its commands land. That context used to be assembled four times: by the runner
when a turn starts, by the resume path when an approved tool runs as the user,
on every Agent Host MCP call, and for the pod MCP server. Each copy drifted:
the MCP copy once left ``workload_id`` unset and authorized every Agent Host
tool call as the pod's assistant.

Now every path loads a ``RunSession`` and asks it for the context:

* the runner (``RunSession.load``) reads the conversation, agent, run and
  history window in one short unit of work, resolves the runtime through the
  one chain, reads the grants, and builds the tools' context
  (``turn_context``) with the run's briefs (``context_brief.run_briefs``);
* the resume path (``RunSession.for_resume``) rebuilds it for the paused run;
* the Agent Host MCP path (``agent_host/relayed_session.load_relayed_session``) keeps
  the runtime and grants cached for the run's lease by grant generation, so a
  call does not re-derive what the session already holds;
* the pod MCP server builds a pod-scoped context, with no run
  (``pod_tool_context``).

The fields every variant shares are set in one place (``_identity``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Protocol, TypedDict
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.agent_memory_paths import (
    memory_is_active,
    memory_kept_after_turn,
)
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation, Message
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.ports import AgentRepository as AgentRepositoryPort
from app.modules.agent.domain.private_notes import (
    keeper_started,
    run_delivers_to_surface,
)
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.run_origin import run_origin
from app.modules.agent.domain.runtime_choice import RuntimePurpose
from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
from app.modules.agent.domain.value_objects import (
    AgentToolset,
    HarnessKind,
    JsonObject,
)
from app.modules.agent.domain.vision import AgentVisionMode, resolve_vision_mode
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.domain.workspace_location import (
    WorkspaceLocation,
    has_recorded_cwd,
    pod_cwd_from_workspace_cwd,
    resolve_workspace_location,
)
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.history_reads import HistoryReads
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.channel_background import with_channel_background
from app.modules.agent.services.conversation_access import (
    resolve_agent,
    validate_conversation_access,
)
from app.modules.agent.services.run_phase_spans import record_history_size, run_phase
from app.modules.agent.domain.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    assemble_runtime_history,
    without_private_runs,
)
from app.modules.agent.services.runtime_resolution import (
    ResolveConfig,
    RuntimeRequest,
    resolve_runtime,
)
from app.modules.agent.services.surface_context import (
    conversation_surface,
    surface_context_for_run,
)
from app.modules.agent.services.vision_service import vision_delegate_available
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.callables import grant_summary_of
from app.modules.agent.domain.toolsets import (
    AgentGrantSummary,
    resolve_toolset_names,
)

if TYPE_CHECKING:
    from app.modules.agent_surfaces.contracts.conversations import (
        ConversationSurface,
    )

#: The connector accounts an agent's tools may use, read for the person.
ConfiguredAccounts = Callable[..., Awaitable[dict[str, UUID]]]


class SessionLoader(Protocol):
    """``RunSession.load``'s shape, so a runner can be given another."""

    def __call__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> Awaitable[RunSession]: ...


#: The conversation a pod-scoped context stands in for: there is none.
NIL_CONVERSATION_ID = UUID(int=0)


class _Identity(TypedDict):
    """The tool-context fields every variant of a run's context shares."""

    user_id: UUID
    org_id: UUID | None
    pod_id: UUID
    conversation_id: UUID
    agent_name: str | None
    agent_run_id: UUID | None
    workload_type: str | None
    workload_id: UUID | None
    is_pod_default_agent: bool
    runtime_profile: JsonObject | None
    workspace_id: str
    workspace_cwd: str
    workspace_repo: ProjectRepo | None
    pod_cwd: str


@dataclass(slots=True)
class RunSession:
    """One run's conversation, agent, runtime, grants and history, loaded once."""

    conversation: Conversation
    agent: Agent | None
    run: AgentRun | None
    workspace: WorkspaceLocation
    history: list[Message] = field(default_factory=list)
    runtime: ResolvedAgentRuntime | None = None
    #: The runtime as the tools see it (``RuntimeSnapshot.as_payload``).
    runtime_profile: JsonObject | None = None
    grants: AgentGrantSummary | None = None
    #: Where agent_surfaces last heard the conversation, if anywhere.
    surface: ConversationSurface | None = None
    #: The run's place in its conversation (``RuntimeHistoryWindow``).
    run_index: int | None = None

    # -- Loading ------------------------------------------------------------

    @classmethod
    def of(
        cls,
        conversation: Conversation,
        agent: Agent | None,
        run: AgentRun | None,
        history: list[Message] | None = None,
        surface: ConversationSurface | None = None,
    ) -> RunSession:
        """A session over rows already read, its workspace resolved from them."""
        return cls(
            conversation=conversation,
            agent=agent,
            run=run,
            workspace=resolve_workspace_location(conversation),
            history=list(history or []),
            surface=surface,
        )

    @classmethod
    async def load(
        cls,
        uow_factory: UnitOfWorkFactory,
        *,
        agent_run_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
    ) -> RunSession:
        """A run's start: conversation, agent, run and history, in one unit of work."""
        with run_phase("load_context") as span:
            async with uow_factory() as uow:
                conversations = Conversations(uow)
                history_reads = HistoryReads(uow)
                window = await history_reads.load_runtime_history_digests_by_run_id(
                    agent_run_id, limit=MAX_HISTORY_AGENT_RUNS
                )
                runs = window.runs
                # In the window or not -- see `RuntimeHistoryWindow`.
                agent_run = window.current_run
                if agent_run is None:
                    raise ConversationNotFoundError()
                conversation = validate_conversation_access(
                    await conversations.get_conversation(agent_run.conversation_id),
                    user_id=user_id,
                    pod_id=pod_id,
                )
                agent = await resolve_agent(
                    conversation,
                    user_id=user_id,
                    agent_repository=AgentRepository(uow),
                    agent_name=agent_name,
                )
                messages = await assemble_runtime_history(
                    history_reads,
                    window,
                    conversation_id=agent_run.conversation_id,
                    run_id=agent_run.id,
                    read_channel=partial(with_channel_background, uow),
                )
                if (
                    Audience.of(conversation).answers_outsiders
                    and not agent_run.state.private_note
                ):
                    messages = without_private_runs(
                        messages, runs, current_run_id=agent_run.id
                    )
                surface = await conversation_surface(uow, conversation.id)
                record_history_size(span, runs=runs, sent=messages)
        session = cls.of(conversation, agent, agent_run, messages, surface)
        session.run_index = window.current_run_index
        return session

    @classmethod
    async def for_resume(
        cls,
        uow: SqlAlchemyUnitOfWork,
        *,
        conversation: Conversation,
        user_id: UUID,
        agent_repository: AgentRepositoryPort,
        uow_factory: UnitOfWorkFactory,
    ) -> RunSession:
        """A paused run's session, rebuilt so an approved tool runs as in-run.

        The runtime is the one a turn would get now (``turn_runtime``), on the
        caller's unit of work; it is resolved on a unit of its own.
        """
        from app.modules.agent.services.runtime_availability import turn_runtime

        agent = await resolve_agent(
            conversation, user_id=user_id, agent_repository=agent_repository
        )
        selected = await turn_runtime(uow, conversation, agent)
        resolved = await resolve_runtime(
            RuntimeRequest(
                organization_id=conversation.organization_id,
                user_id=user_id,
                configured=selected,
            ),
            purpose=RuntimePurpose.TURN,
            uow_factory=uow_factory,
        )
        assert resolved is not None  # a turn's runtime resolves or raises
        return cls(
            conversation=conversation,
            agent=agent,
            run=None,
            workspace=resolve_workspace_location(conversation),
            runtime=resolved,
            runtime_profile=resolved.runtime_snapshot().as_payload(),
        )

    async def resolve_turn_runtime(
        self, *, user_id: UUID, uow_factory: UnitOfWorkFactory, resolve: ResolveConfig
    ) -> ResolvedAgentRuntime:
        """The run's runtime -- one that runs in this process, for a stranger's turn.

        A stranger's turn never reaches a coding agent's shell, which would hold
        a token minted for the member who answers for the group.
        """
        assert self.run is not None
        resolved = await resolve_runtime(
            RuntimeRequest(
                organization_id=self.conversation.organization_id,
                user_id=user_id,
                configured=self.run.agent_runtime,
                answers_outsiders=Audience.of(self.conversation).answers_outsiders,
            ),
            purpose=RuntimePurpose.TURN,
            resolve=resolve,
            uow_factory=uow_factory,
        )
        assert resolved is not None  # a turn always resolves or raises
        self.runtime = resolved
        self.runtime_profile = resolved.runtime_snapshot().as_payload()
        return resolved

    # -- The context the tools see -------------------------------------------

    def _identity(self, *, user_id: UUID, agent_run_id: UUID | None) -> _Identity:
        """What every variant of a run's tool context says the same way.

        ``workload_id`` is the principal ``pod_data_access`` builds its delegated
        context from: left unset it means "the pod default assistant", which
        runs with the invoking user's pod permissions rather than this agent's
        grants. The location fields keep tools in the directory the agent's own
        prompt names, and ``pod_cwd`` mirrors it on the pod filesystem.
        """
        agent = self.agent
        return {
            "user_id": user_id,
            "org_id": self.conversation.organization_id,
            "pod_id": self.conversation.pod_id,
            "conversation_id": self.conversation.id,
            "agent_name": agent.name if agent is not None else None,
            "agent_run_id": agent_run_id,
            "workload_type": "agent",
            "workload_id": agent.id if agent is not None else None,
            "is_pod_default_agent": agent is None
            or agent.kind is AgentKind.POD_DEFAULT,
            "runtime_profile": self.runtime_profile,
            "workspace_id": self.workspace.workspace_id,
            "workspace_cwd": self.workspace.cwd,
            "workspace_repo": self.workspace.repo,
            "pod_cwd": pod_cwd_from_workspace_cwd(self.workspace.cwd),
        }

    async def turn_context(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        user_id: UUID,
        configured_accounts: ConfiguredAccounts,
    ) -> ToolContext:
        """The context this run's tools execute against, briefs included.

        Built once, before the model is called, because several of these settle
        each other: the vision mode before the toolset is assembled (the
        assembler offers `view_image` on it), and the workspace cwd written down
        before any tool resolves a relative path against it.
        """
        assert self.agent is not None and self.run is not None
        assert self.runtime is not None
        conversation, agent, agent_run = self.conversation, self.agent, self.run
        in_process = self.runtime.harness_kind == HarnessKind.LEMMA
        await self._record_cwd(uow_factory)
        # From the run's *effective* toolsets, not the agent's configured ones:
        # the always-on set, the grant-derived ones and the sub-agent
        # withholding are all accounted for -- the list `tool_catalog.specs_for`
        # builds tools from. The grants are kept so it need not read them again.
        grant_rows = await self._load_grants(uow_factory)
        run_toolsets, _ = resolve_toolset_names(agent, conversation, grants=self.grants)
        surface_context = surface_context_for_run(self.surface, agent_run.state)
        audience = Audience.of(conversation)
        for_outsider = audience.answers_outsiders
        memory_enabled = memory_is_active(run_toolsets)
        ctx = ToolContext(
            **self._identity(user_id=user_id, agent_run_id=agent_run.id),
            # The owner's connector accounts are theirs; a stranger's run has
            # no connector tools to use them with, and must not be handed them.
            configured_accounts=(
                {}
                if for_outsider
                else await configured_accounts(agent=agent, user_id=user_id)
            ),
            # Only the in-process harness catches the pause signal; remote
            # harnesses run the tools over MCP and use the WAITING contract.
            supports_pause_signal=in_process,
            memory_enabled=memory_enabled,
            memory_after_turn=agent_settings.agent_memory_after_turn
            and memory_kept_after_turn(
                memory_enabled=memory_enabled,
                in_process=in_process,
                answers_outsiders=for_outsider,
                origin=run_origin(conversation).origin,
            ),
            run_toolsets=run_toolsets,
            workspace_readable=AgentToolset.WORKSPACE_CLI in run_toolsets,
            grant_summary=self.grants,
            audience=audience,
            delivers_to_surface=run_delivers_to_surface(agent_run.state),
            keeper_asking=for_outsider and keeper_started(agent_run.state),
            **surface_context,
        )
        from app.modules.agent.services.context_brief.run_briefs import run_briefs

        await run_briefs(
            ctx,
            uow_factory,
            conversation=conversation,
            agent=agent,
            agent_run=agent_run,
            user_id=user_id,
            run_toolsets=run_toolsets,
            platform=surface_context["surface_platform"],
            grant_rows=grant_rows,
        )
        # How image-returning tools answer on this run. Settled before the
        # toolset is built: `view_image` is offered whenever the mode can
        # answer at all.
        ctx.vision_mode = self.vision_mode()
        if for_outsider:
            # No commands run for a stranger, so there is nowhere to run them --
            # and the owner's Mac is the last place one should land.
            return ctx
        await self._settle_host(ctx, user_id=user_id)
        return ctx

    async def _load_grants(
        self, uow_factory: UnitOfWorkFactory
    ) -> list[tuple[str, UUID, str]] | None:
        """The agent's grant rows, read once for its toolsets and its brief.

        ``None`` where it cannot have grants: the pod default assistant has no
        row of its own and runs with the user's permissions.
        """
        agent = self.agent
        assert agent is not None
        if agent.pod_id is None or agent.id is None:
            self.grants = AgentGrantSummary()
            return None
        async with uow_factory() as uow:
            rows = await AgentContextBriefRepository(uow).get_agent_grants(
                pod_id=agent.pod_id, agent_id=agent.id
            )
        self.grants = grant_summary_of(rows, agent_id=agent.id)
        return rows

    def vision_mode(self) -> AgentVisionMode:
        assert self.runtime is not None
        return resolve_vision_mode(
            model_supports_vision=RuntimeModelCapability.VISION
            in self.runtime.capabilities,
            delegate_model_configured=vision_delegate_available(),
        )

    async def _record_cwd(self, uow_factory: UnitOfWorkFactory) -> None:
        """A conversation that never had a cwd written down gets one now.

        The same rule the MCP bridge follows, so the row is the source of truth
        in fact. Costs nothing on the common path: creation stamps it.
        """
        if has_recorded_cwd(self.conversation):
            return
        async with uow_factory() as uow:
            await Conversations(uow).record_cwd(
                self.conversation.id, self.workspace.cwd
            )
            await uow.commit()
        self.conversation.cwd = self.workspace.cwd

    async def _settle_host(self, ctx: ToolContext, *, user_id: UUID) -> None:
        """Where this run's commands execute, decided once, here, for the run.

        Imported here to keep host selection (and the workspace module's host
        provider) out of every process's startup import graph.
        """
        from app.modules.agent.agent_host.api import (
            choose_host_workspace,
        )
        from app.modules.agent.agent_host.api import (
            settle_native_commands,
        )

        assert self.run is not None and self.runtime is not None
        if self.runtime.harness_kind == HarnessKind.LEMMA:
            ctx.host_workspace = await choose_host_workspace(
                conversation=self.conversation, agent_run=self.run, user_id=user_id
            )
            return
        ctx.host_runs_native_commands = await settle_native_commands(
            conversation=self.conversation,
            agent_run=self.run,
            harness_id=_harness_id(self.runtime_profile),
        )

    async def resume_context(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        configured_accounts: ConfiguredAccounts,
    ) -> ToolContext:
        """The paused run's context, for an approved tool run as the user.

        Surface delivery is omitted -- approval-gated tools don't deliver to
        surfaces -- and the vision mode is resolved exactly as a run resolves
        it, or an approved `view_image` would tell the user a model that reads
        images cannot.
        """
        from app.modules.agent.agent_host.api import (
            recorded_host_workspace,
        )

        assert self.agent is not None
        return ToolContext(
            vision_mode=self.vision_mode(),
            **self._identity(user_id=user_id, agent_run_id=agent_run_id),
            configured_accounts=await configured_accounts(
                agent=self.agent, user_id=user_id
            ),
            # The paused run's own recorded choice, never a new one: an
            # approved command must land where the run was executing.
            host_workspace=await recorded_host_workspace(agent_run_id),
        )

    def relayed_context(self, *, agent_run_id: UUID | None) -> ToolContext:
        """The context one relayed (Agent Host MCP) tool call runs against.

        It is the tool path for *every* remote harness, so anything left at its
        default is a default the whole of Agent Host runs on: the vision mode
        (or a harness that reads images delegates work it could do itself),
        the audience (a remote harness answering an outsider authorizes as
        nobody too), and surface delivery (a run a private note started
        delivers nothing to the platform from here either).
        """
        from app.modules.agent.domain.vision import vision_mode_from_runtime_profile

        run = self.run
        return ToolContext(
            **self._identity(
                user_id=self.conversation.user_id,
                agent_run_id=agent_run_id or (run.id if run is not None else None),
            ),
            audience=Audience.of(self.conversation),
            delivers_to_surface=run_delivers_to_surface(
                run.state if run is not None else None
            ),
            vision_mode=vision_mode_from_runtime_profile(
                self.runtime_profile,
                delegate_model_configured=vision_delegate_available(),
            ),
            **surface_context_for_run(
                self.surface, run.state if run is not None else None
            ),
        )


def pod_tool_context(
    *,
    user_id: UUID,
    pod_id: UUID,
    workload_type: str | None = None,
    workload_id: UUID | None = None,
    agent_name: str | None = None,
    is_pod_default_agent: bool = True,
    delegation_scope: frozenset[str] = frozenset(),
) -> ToolContext:
    """The pod-scoped variant: a pod token's tools, with no run behind them.

    There is no run here, and so no runtime profile to ask -- this serves an
    MCP client holding a pod token. DIRECT rather than the UNAVAILABLE default,
    because MCP has a first-class image content type: the client decides what
    to do with an image, and one that cannot read it still gets the same JSON
    answer beside it.
    """
    return ToolContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=NIL_CONVERSATION_ID,
        workload_type=workload_type,
        workload_id=workload_id,
        agent_name=agent_name,
        is_pod_default_agent=is_pod_default_agent,
        delegation_scope=delegation_scope,
        vision_mode=AgentVisionMode.DIRECT,
    )


def _harness_id(snapshot: JsonObject | None) -> UUID | None:
    """The Agent Host harness a coding-agent run's profile names, if any."""
    raw = (snapshot or {}).get("harness_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


__all__ = [
    "NIL_CONVERSATION_ID",
    "ConfiguredAccounts",
    "RunSession",
    "SessionLoader",
    "pod_tool_context",
]

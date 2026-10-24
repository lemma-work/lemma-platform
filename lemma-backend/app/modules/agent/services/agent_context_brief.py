"""Builds the runtime-context brief appended to an agent's system prompt.

The brief grounds the agent in its environment without it having to run any
discovery commands: the current pod, the current user, the resources it can work
with — for the pod default assistant the full pod inventory (a server-side ``pod
describe``), for a user-created agent only the resources granted to it, each
with name, description, and (for tables) schema — and, for an agent with the
MEMORY capability, what it already knows.

That last part is built and cached by ``agent_memory_brief`` and appended here
rather than assembled inline, because it is the only half that goes stale from
the agent's own writes: it is invalidated on write, while everything below only
changes when somebody edits the pod.

Connection discipline: each DB read runs in its own short UoW that is released
immediately, and storage I/O (the file walk) is isolated in its own UoW so it
never extends a span. The whole rendered brief is cached per
(agent, pod, user, is_default) for ``agent_context_brief_cache_ttl_seconds``, so
a user's repeated runs against the same agent skip the build (and the DB)
entirely -- across conversations, not just within one. The memory section has
its own entry and its own TTL, for the reason above.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.core.authorization.current import reset_current_context, set_current_context
from app.modules.agent.domain.agent_kind import AgentKind
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident
from app.modules.agent.domain.agent_memory_paths import memory_is_active
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.config import agent_settings
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.agent_memory_brief import AgentMemoryBriefBuilder
from app.modules.agent.services.granted_resources_brief import GrantedResourcesBrief
from app.modules.agent.services.brief_lines import (
    MAX_RESOURCES as _MAX_RESOURCES,
    more_note,
    table_line,
    top_level_file_entries,
    run_framing,
    user_lines,
)
from app.modules.agent.services.agent_self_brief import (
    AgentSelfBriefBuilder,
    READ_FAILED,
)
from app.modules.agent.services.run_phase_spans import run_phase
from app.modules.datastore.contracts.agent_tools import (
    build_file_service,
    build_table_service,
)
from app.modules.function.contracts import agent_tools as function_tools
from app.modules.pod.contracts.directory import list_pod_members
from app.modules.agent.services.askable_pods_brief import askable_pod_lines
from app.core.authorization.factory import create_authorization_data_service
from app.modules.agent.services.brief_seams import (
    AuthorizationFactory,
    RepositoryFactory,
)

_MAX_TABLES = 50
_MAX_MEMBERS = 25

# Redis-backed cache of rendered briefs, keyed by
# (agent, pod, user, is_default). Redis rather than an in-process dict so it is
# shared across the API, the worker and replicas with no per-process staleness;
# Redis being unavailable degrades to a miss and never fails a run.
#
# Deliberately NOT keyed by conversation. It used to be, and that made the cache
# almost dead: most runs are the first run of their conversation and therefore a
# guaranteed miss, and the runs that were not typically arrived long enough after
# the previous one to have outlived the TTL anyway. So the hot path paid a full
# rebuild on nearly every run to protect against variation that does not exist.
#
# Nothing in the rendered brief is conversation-derived. The build reads the pod,
# the user and either the pod inventory or the agent's grants; the only thing it
# takes from the conversation is whether this is the pod default assistant, which
# selects between those two branches -- so that boolean belongs in the key and
# the conversation id does not.
_BriefKey = tuple[UUID, UUID, UUID, bool]
_brief_cache: RedisJsonCache | None = None

logger = get_logger(__name__)
# Read and written on every conversation's context assembly, so a Redis outage
# would be one record per run for a condition that is uniform. This emits one
# degraded/recovered pair per incident instead — the alternative, staying
# silent, is how a cache that has been down for hours degrades every
# conversation with nothing to show for it.
_brief_cache_incident = DependencyIncident("agent_context_brief_cache", logger=logger)


def _get_brief_cache() -> RedisJsonCache | None:
    global _brief_cache
    ttl = agent_settings.agent_context_brief_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _brief_cache is None or _brief_cache._ttl_seconds != ttl:
        _brief_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            # Versioned: an entry is now the two halves below, and an entry
            # in the old one-string shape must read as a miss, not a brief.
            key_prefix="agent:context-brief:v2",
            ttl_seconds=ttl,
        )
    return _brief_cache


class _Inventory(BaseModel):
    """What the cache holds: the brief, split by how often each half changes.

    ``files`` is the top-level file listing, which ordinary work changes -- an
    upload, a report the agent saved -- so it goes in the run's context part
    rather than the system prompt. Everything in ``stable`` changes only when
    somebody reshapes the pod.
    """

    stable: str
    files: str = ""


@dataclass(frozen=True, slots=True)
class ContextBrief:
    """The runtime brief in its two halves.

    ``stable`` is appended to the system prompt and holds for the conversation;
    ``per_run`` -- what started this run, the file listing, memory -- changes
    between runs, and rides in the run's context part instead. Kept apart
    because a changed byte in the system prompt re-reads the whole history.
    """

    stable: str
    per_run: str


def _cache_suffix(key: _BriefKey) -> str:
    return ":".join(str(part) for part in key)


async def _get_cached_brief(key: _BriefKey) -> str | None:
    cache = _get_brief_cache()
    if cache is None:
        return None
    try:
        cached = await cache.get_raw(_cache_suffix(key))
    except Exception as exc:
        # Redis unavailable -> treat as a cache miss; never fail a run.
        _brief_cache_incident.record_failure(error_type=type(exc).__name__)
        return None
    _brief_cache_incident.record_success()
    return cached


async def _set_cached_brief(key: _BriefKey, brief: str) -> None:
    cache = _get_brief_cache()
    if cache is None:
        return
    try:
        await cache.set_raw(_cache_suffix(key), brief)
    except Exception as exc:
        # Redis unavailable -> skip caching; never fail a run.
        _brief_cache_incident.record_failure(error_type=type(exc).__name__)
        return
    _brief_cache_incident.record_success()


def _member_line(member) -> str:
    """One person, with the id `channel_post` actually takes."""
    who = member.name or member.email or "(unnamed)"
    email = f" <{member.email}>" if member.name and member.email else ""
    role = f" — {member.role}" if member.role else ""
    you = " — this is the person you are talking to" if member.is_you else ""
    return f"- {who}{email}{role}{you} (to: {member.to})"


def _decoded(raw: str | None) -> _Inventory | None:
    """A cache entry, or None when there is none or it is not this shape."""
    if raw is None:
        return None
    try:
        return _Inventory.model_validate_json(raw)
    except ValidationError:
        return None


class AgentContextBriefBuilder:
    """Assembles the runtime brief.

    ``repository`` is a constructor seam, and the self-brief it builds takes the
    same one: a double patched into either module sits inside the subject rather
    than in front of a collaborator, and goes on passing through a rename that
    ought to have failed.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        repository: RepositoryFactory | None = None,
        authorization: AuthorizationFactory | None = None,
    ):
        self.uow_factory = uow_factory
        # Resolved at call time, not bound here: a default argument is
        # evaluated once at import, so a test replacing the module name
        # afterwards never reaches it -- and the test still passes,
        # because a real collaborator failing looks like a fake one
        # failing. `None` means "whatever the module says when asked".
        self._repository = repository
        self._authorization = authorization

    def _repo_factory(self) -> RepositoryFactory:
        return self._repository or AgentContextBriefRepository

    def _authz_factory(self) -> AuthorizationFactory:
        return self._authorization or create_authorization_data_service

    async def build(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        user_id: UUID,
        pod_id: UUID,
        toolsets: Collection[AgentToolset] = (),
        run_source: str | None = None,
        platform: str | None = None,
    ) -> ContextBrief:
        # The pod default assistant runs with the user's permissions and sees the
        # whole pod; named agents see only what they're granted. This is the one
        # thing the conversation contributes, so it is resolved into the key.
        is_default = agent.kind is AgentKind.POD_DEFAULT
        with run_phase("context_brief") as span:
            key: _BriefKey = (agent.id, pod_id, user_id, is_default)
            cached = _decoded(await _get_cached_brief(key))
            span.set_attribute("lemma.cache_hit", cached is not None)
            if cached is None:
                cached = await self._build_uncached(
                    key,
                    agent=agent,
                    is_default=is_default,
                    user_id=user_id,
                    pod_id=pod_id,
                )
            # Outside the cache, and for the same reason memory is: this varies
            # per conversation, and the cache is deliberately not keyed by one.
            memory = await self._memory_section(
                agent=agent, pod_id=pod_id, user_id=user_id, toolsets=toolsets
            )
            asks: list[str] = []
            if is_default:
                # Per conversation too: which other pods this thread may ask,
                # and how, depends on who reads it and whether anyone is here.
                asks = await askable_pod_lines(
                    self.uow_factory,
                    self._repo_factory(),
                    conversation=conversation,
                    pod_id=pod_id,
                    user_id=user_id,
                )
            per_run = (
                run_framing(conversation, run_source=run_source, platform=platform),
                "\n".join(asks),
                cached.files,
                memory,
            )
            return ContextBrief(
                stable=cached.stable,
                per_run="\n\n".join(part.strip() for part in per_run if part.strip()),
            )

    async def _memory_section(
        self,
        *,
        agent: Agent,
        pod_id: UUID,
        user_id: UUID,
        toolsets: Collection[AgentToolset],
    ) -> str:
        """The memory section, which is cached and invalidated apart.

        Outside the inventory cache above, not inside it: memory changes when
        this agent writes a fact mid-conversation, and baking it into a 60s
        entry is exactly how an agent ends up unable to recall what it just
        learned.

        Gated on the same predicate the prompt fragment uses: an agent with no
        way to reach a pod file is never shown folders it cannot write to.
        """
        if not memory_is_active(toolsets):
            return ""
        return await AgentMemoryBriefBuilder(self.uow_factory).build(
            agent=agent, pod_id=pod_id, user_id=user_id
        )

    async def _build_uncached(
        self,
        key: _BriefKey,
        *,
        agent: Agent,
        is_default: bool,
        user_id: UUID,
        pod_id: UUID,
    ) -> _Inventory:
        # uow 1: plain identity reads (no authorization context needed).
        async with self.uow_factory() as uow:
            repo = self._repo_factory()(uow)
            pod = await repo.get_pod_profile(pod_id)
            profile = await repo.get_user_profile(user_id)
        lines = [
            "# Runtime Context",
            f"- Pod: {pod.name or '(unknown)'} ({pod_id})",
        ]
        if pod.description and pod.description.strip():
            # The one sentence the team wrote about their own work. The brief
            # named the pod and never said what it was for, so an agent had the
            # id of a thing it could not describe.
            lines.append(f"- What this pod is for: {pod.description.strip()}")
        if pod.owns:
            # What the pod owns and how it is counted. The plan for getting there
            # is a file the line points at, read when the work needs it rather
            # than carried in every prompt.
            lines.append(f"- What this pod owns: {pod.owns}")
        lines.extend(user_lines(profile, user_id))

        lines.extend(
            await AgentSelfBriefBuilder(
                self.uow_factory,
                repository=self._repository,
                authorization=self._authorization,
            ).build(
                agent=agent,
                pod=pod,
                pod_id=pod_id,
                user_id=user_id,
                is_default=is_default,
            )
        )

        files: list[str] = []
        if is_default:
            lines.extend(await self._pod_inventory(pod_id=pod_id, user_id=user_id))
            files = await self._file_lines(pod_id=pod_id, user_id=user_id)
        else:
            lines.extend(
                await GrantedResourcesBrief(
                    self.uow_factory,
                    repository=self._repo_factory(),
                    authorization=self._authz_factory(),
                ).lines(agent=agent, pod_id=pod_id, user_id=user_id)
            )

        inventory = _Inventory(stable="\n".join(lines), files="\n".join(files))
        await _set_cached_brief(key, inventory.model_dump_json())
        return inventory

    async def _pod_inventory(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        """Everything in this pod, one section per kind of resource.

        One method per section rather than one long one: each opens its own
        short uow, each is best-effort or not on its own terms, and the list
        grew from four kinds to six when workflows and the people were added.
        """
        lines: list[str] = []
        lines.extend(await self._table_lines(pod_id=pod_id, user_id=user_id))
        lines.extend(await self._agent_lines(pod_id=pod_id))
        lines.extend(await self._people_lines(pod_id=pod_id, user_id=user_id))
        lines.extend(await self._workflow_lines(pod_id=pod_id, user_id=user_id))
        lines.extend(await self._function_lines(pod_id=pod_id))
        return lines

    async def _table_lines(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        # The datastore read needs the authorization context; build ctx in this
        # uow and render the rows (lazy column access) before it closes.
        async with self.uow_factory() as uow:
            ctx = await self._authz_factory()(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            token = set_current_context(ctx)
            try:
                tables, table_total = await build_table_service(uow).list_tables(
                    pod_id, ctx, limit=_MAX_TABLES
                )
                rendered = [table_line(table) for table in tables]
                rendered.extend(more_note(len(tables), table_total, "tables"))
            finally:
                reset_current_context(token)
        return (
            [
                "\n## Tables",
                "Compact schemas; describe a table for column descriptions.",
                *rendered,
            ]
            if rendered
            else []
        )

    async def _agent_lines(self, *, pod_id: UUID) -> list[str]:
        async with self.uow_factory() as uow:
            agents, agent_total = await AgentRepository(uow).list_by_pod(
                pod_id=pod_id, limit=_MAX_RESOURCES
            )
        # The pod's own agent has a row now, so it comes back in this listing --
        # and without this it would offer itself as an agent to delegate to.
        named = [a for a in agents if a.kind is not AgentKind.POD_DEFAULT]
        if not named:
            return []
        return [
            "\n## Agents",
            *(
                f"- {a.name}" + (f" — {a.description}" if a.description else "")
                for a in named
            ),
            *more_note(len(agents), agent_total, "agents"),
        ]

    async def _people_lines(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        """Who else is here, and the id each of them is addressed by.

        Absent from the brief entirely until now, which is why the messaging
        fragment has to open by telling the agent to go and find out who it is
        talking about: `channel_post` takes an id, and an agent told "ask Priya"
        had no way to turn that into one without a tool call.

        The contract runs under this user's own authority, so an agent can never
        enumerate a pod its invoker cannot see.
        """
        try:
            directory = await list_pod_members(
                pod_id=pod_id, requester_user_id=user_id, limit=_MAX_MEMBERS
            )
        except READ_FAILED:
            logger.warning(
                "agent.context_brief.member_directory_unavailable.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
            return []
        if directory is None or not directory.members:
            return []
        lines = [
            "\n## People here",
            (
                "- Pass the `to` value verbatim to `channel_post`; a name will "
                "not resolve."
            ),
            *(_member_line(member) for member in directory.members),
        ]
        if directory.truncated:
            lines.append(
                f"- … and {directory.total_matched - len(directory.members)} more "
                "members not listed here. Use `list_pod_members`."
            )
        return lines

    async def _workflow_lines(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        """The one automation primitive the brief never named.

        An agent could see the functions and the schedules but not the processes
        wired between them, and proposed building one that already existed.

        Filtered by the invoking user's context: a workflow carries its own
        visibility and owner, so being in the pod is not the same as being able
        to read every workflow in it.
        """
        async with self.uow_factory() as uow:
            ctx = await self._authz_factory()(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            workflows, total = await self._repo_factory()(uow).list_workflows(
                pod_id=pod_id, ctx=ctx, limit=_MAX_RESOURCES
            )
        if not workflows:
            return []
        return [
            "\n## Workflows",
            *(
                f"- {w.name}"
                + (f" — {w.description}" if w.description else "")
                + ("" if w.is_active else " (inactive)")
                for w in workflows
            ),
            *more_note(len(workflows), total, "workflows"),
        ]

    async def _function_lines(self, *, pod_id: UUID) -> list[str]:
        async with self.uow_factory() as uow:
            functions, total = await function_tools.list_pod_functions(
                uow, pod_id, limit=_MAX_RESOURCES
            )
        if not functions:
            return []
        return [
            "\n## Functions",
            *(
                f"- {f.name} [{getattr(f.type, 'value', f.type)}]"
                + (f" — {f.description}" if f.description else "")
                for f in functions
            ),
            *more_note(len(functions), total, "functions"),
        ]

    async def _file_lines(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        """Best-effort grounding, in its own uow so the storage walk is alone."""
        try:
            async with self.uow_factory() as uow:
                ctx = await self._authz_factory()(uow).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                token = set_current_context(ctx)
                try:
                    tree = await build_file_service(uow).get_directory_tree(
                        pod_id, ctx, root_path="/", files_per_directory=5
                    )
                finally:
                    reset_current_context(token)
        except Exception:
            # Files are best-effort context; never fail prompt assembly on them
            # -- but say so. A silently missing "Files (top level)" section reads
            # to the model as a pod with no files in it.
            logger.warning(
                "agent.context_brief.file_inventory_unavailable.degraded",
                pod_id=str(pod_id),
                exc_info=True,
            )
            return []
        entries = top_level_file_entries(tree)
        return (
            ["\n## Files (top level)", *(f"- {entry}" for entry in entries)]
            if entries
            else []
        )

"""Builds the runtime-context brief appended to an agent's system prompt.

The brief grounds the agent in its environment without it having to run any
discovery commands: the current pod, the current user, the AGENTS.md content
from its four memory scopes (see ``agent_memory_paths``), and the resources it
can work with — for the pod default assistant the full pod inventory (a
server-side ``pod describe``), for a user-created agent only the resources
granted to it, each with name, description, and (for tables) schema.

Connection discipline: each DB read runs in its own short UoW that is released
immediately, and storage I/O (the file walk) is isolated in its own UoW so it
never extends a span. The whole rendered brief is cached per
(agent, pod, user, is_default) for ``agent_context_brief_cache_ttl_seconds``, so
a user's repeated runs against the same agent skip the build (and the DB)
entirely -- across conversations, not just within one.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.config import agent_settings
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.run_phase_spans import run_phase
from app.composition.agent_datastore import (
    build_file_service,
    build_table_service,
)
from app.modules.datastore.contracts import (
    DatastoreAccessDeniedError,
    DatastoreFileNotFoundError,
)
from app.composition.agent_functions import create_function_repository
from app.composition.authorization import create_authorization_service

_MAX_TABLES = 50
_MAX_RESOURCES = 50
_MAX_COLUMNS = 40

# Redis-backed cache of rendered briefs, keyed by
# (agent, pod, user, is_default). Redis rather than an in-process dict so it is
# shared across the API, the worker and replicas with no per-process staleness;
# Redis being unavailable degrades to a miss and never fails a run.
#
# Deliberately NOT keyed by conversation. It used to be, and that made the cache
# almost dead: 89.9% of production runs are the first run of their conversation
# and therefore a guaranteed miss, and of the runs that were not, the median gap
# to the previous one was 426.9s -- seven times the TTL. So the hot path paid a
# full rebuild on ~90% of runs to protect against variation that does not exist.
#
# Nothing in the rendered brief is conversation-derived. The build reads the pod,
# the user and either the pod inventory or the agent's grants; the only thing it
# takes from the conversation is whether this is the pod default assistant, which
# selects between those two branches -- so that boolean belongs in the key and
# the conversation id does not.
_BriefKey = tuple[UUID, UUID, UUID, bool]
_brief_cache: RedisJsonCache | None = None


def _get_brief_cache() -> RedisJsonCache | None:
    global _brief_cache
    ttl = agent_settings.agent_context_brief_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _brief_cache is None or _brief_cache._ttl_seconds != ttl:
        _brief_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="agent:context-brief",
            ttl_seconds=ttl,
        )
    return _brief_cache


def _cache_suffix(key: _BriefKey) -> str:
    return ":".join(str(part) for part in key)


async def _get_cached_brief(key: _BriefKey) -> str | None:
    cache = _get_brief_cache()
    if cache is None:
        return None
    try:
        return await cache.get_raw(_cache_suffix(key))
    except Exception:
        # Redis unavailable -> treat as a cache miss; never fail a run.
        return None


async def _set_cached_brief(key: _BriefKey, brief: str) -> None:
    cache = _get_brief_cache()
    if cache is None:
        return
    try:
        await cache.set_raw(_cache_suffix(key), brief)
    except Exception:
        # Redis unavailable -> skip caching; never fail a run.
        pass


class AgentContextBriefBuilder:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build(
        self,
        *,
        agent: Agent,
        conversation: Conversation,
        user_id: UUID,
        pod_id: UUID,
    ) -> str:
        # The pod default assistant runs with the user's permissions and sees the
        # whole pod; named agents see only what they're granted. This is the one
        # thing the conversation contributes, so it is resolved into the key.
        is_default = conversation.is_pod_assistant or agent.id == DEFAULT_POD_AGENT_ID
        with run_phase("context_brief") as span:
            key: _BriefKey = (agent.id, pod_id, user_id, is_default)
            cached = await _get_cached_brief(key)
            span.set_attribute("lemma.cache_hit", cached is not None)
            if cached is not None:
                return cached
            return await self._build_uncached(
                key, agent=agent, is_default=is_default, user_id=user_id, pod_id=pod_id
            )

    async def _build_uncached(
        self,
        key: _BriefKey,
        *,
        agent: Agent,
        is_default: bool,
        user_id: UUID,
        pod_id: UUID,
    ) -> str:
        # uow 1: plain identity reads (no authorization context needed).
        async with self.uow_factory() as uow:
            repo = AgentContextBriefRepository(uow)
            pod_name = await repo.get_pod_name(pod_id) or "(unknown)"
            email = await repo.get_user_email(user_id)
        user_line = f"{email} ({user_id})" if email else str(user_id)
        lines = [
            "# Runtime Context",
            f"- Pod: {pod_name} ({pod_id})",
            f"- User: {user_line}",
        ]

        lines.extend(
            await self._memory_section(agent=agent, pod_id=pod_id, user_id=user_id)
        )

        if is_default:
            lines.extend(await self._pod_inventory(pod_id=pod_id, user_id=user_id))
        else:
            lines.extend(
                await self._granted_resources(
                    agent=agent, pod_id=pod_id, user_id=user_id
                )
            )

        brief = "\n".join(lines)
        await _set_cached_brief(key, brief)
        return brief

    async def _memory_section(
        self, *, agent: Agent, pod_id: UUID, user_id: UUID
    ) -> list[str]:
        """AGENTS.md content for this agent's four memory scopes, best-effort.

        Runs for every agent, default or named — pod-shared memory is useful
        pod-wide, not just to Lem. States the agent's own scoped folders
        explicitly rather than leaving it to compute the slug itself: a
        self-computed path that drifts from this one would make its own
        writes invisible to its next briefing.
        """
        paths = agent_memory_paths(agent)
        entries = (
            ("Pod (shared)", paths.pod_index),
            (f"{paths.slug} (shared)", paths.pod_agent_index),
            ("This user (private)", paths.personal_index),
            (f"{paths.slug} + this user (private)", paths.personal_agent_index),
        )
        contents = await self._read_agents_mds(
            [path for _, path in entries], pod_id=pod_id, user_id=user_id
        )

        lines = [
            "\n## Your Memory",
            f"Your agent-scoped memory: `{paths.pod_agent_folder}/` (shared "
            f"pod-wide) and `{paths.personal_agent_folder}/` (private to this "
            "user). Pod-shared facts live under `/memory`, private facts about "
            "the current user under `/me`.",
        ]
        for label, path in entries:
            content = contents.get(path)
            if content:
                lines.append(f"\n### {label} — `{path}`\n{content}")
        return lines

    async def _read_agents_mds(
        self, paths: list[str], *, pod_id: UUID, user_id: UUID
    ) -> dict[str, str]:
        """Text for each path that exists and is readable; the rest are omitted.

        One uow for the whole batch — these four reads are one operation
        against one store, like the Tables section below does in a single
        uow. Each read is guarded against the two expected outcomes of an
        agent that hasn't written there yet (``DatastoreFileNotFoundError``)
        or lacks a grant on it (``DatastoreAccessDeniedError``), and against a
        stray non-UTF-8 file, so one bad path doesn't take the others down.
        ``build_user_context`` itself is deliberately left unguarded, same as
        the Tables/Agents/Functions sections below — a real
        authorization-service failure should fail the brief, not hide as an
        empty memory section.
        """
        results: dict[str, str] = {}
        async with self.uow_factory() as uow:
            ctx = await create_authorization_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            token = set_current_context(ctx)
            try:
                file_service = build_file_service(uow)
                for path in paths:
                    try:
                        _, content = await file_service.download_file_content_by_path(
                            pod_id, path, ctx
                        )
                        text = content.decode("utf-8").strip()
                    except (
                        DatastoreFileNotFoundError,
                        DatastoreAccessDeniedError,
                        UnicodeDecodeError,
                    ):
                        continue
                    if text:
                        results[path] = text
            finally:
                reset_current_context(token)
        return results

    async def _pod_inventory(self, *, pod_id: UUID, user_id: UUID) -> list[str]:
        lines: list[str] = []

        # Tables — datastore read needs the authorization context; build ctx in
        # this uow and render the rows (lazy column access) before it closes.
        async with self.uow_factory() as uow:
            ctx = await create_authorization_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            token = set_current_context(ctx)
            try:
                tables, _ = await build_table_service(uow).list_tables(
                    pod_id, ctx, limit=_MAX_TABLES
                )
                table_lines = [_table_line(table) for table in tables]
            finally:
                reset_current_context(token)
        if table_lines:
            lines.append("\n## Tables")
            lines.extend(table_lines)

        # Agents (plain query).
        async with self.uow_factory() as uow:
            agents, _ = await AgentRepository(uow).list_by_pod(
                pod_id=pod_id, limit=_MAX_RESOURCES
            )
        named = [a for a in agents if a.id != DEFAULT_POD_AGENT_ID]
        if named:
            lines.append("\n## Agents")
            lines.extend(
                f"- {a.name}" + (f" — {a.description}" if a.description else "")
                for a in named
            )

        # Functions (plain query).
        async with self.uow_factory() as uow:
            functions, _ = await create_function_repository(uow).list_by_pod(
                pod_id, limit=_MAX_RESOURCES
            )
        if functions:
            lines.append("\n## Functions")
            lines.extend(
                f"- {f.name} [{f.type.value if hasattr(f.type, 'value') else f.type}]"
                + (f" — {f.description}" if f.description else "")
                for f in functions
            )

        # Files — best-effort grounding, isolated in its own uow so the storage
        # walk never extends the spans above. (Removing the storage hold inside
        # this uow is the datastore file-service factory-mode refactor.)
        try:
            async with self.uow_factory() as uow:
                ctx = await create_authorization_service(uow).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                token = set_current_context(ctx)
                try:
                    tree = await build_file_service(uow).get_directory_tree(
                        pod_id, ctx, root_path="/", files_per_directory=5
                    )
                finally:
                    reset_current_context(token)
            entries = _top_level_file_entries(tree)
            if entries:
                lines.append("\n## Files (top level)")
                lines.extend(f"- {entry}" for entry in entries)
        except Exception:
            # Files are best-effort context; never fail prompt assembly on them.
            pass
        return lines

    async def _granted_resources(
        self, *, agent: Agent, pod_id: UUID, user_id: UUID
    ) -> list[str]:
        # uow 1: grants + name resolution (plain queries).
        async with self.uow_factory() as uow:
            repo = AgentContextBriefRepository(uow)
            rows = await repo.get_agent_grants(pod_id=pod_id, agent_id=agent.id)
            if not rows:
                return [
                    "\n## Granted Resources",
                    "- (none) — you have no resource grants yet. If a tool returns "
                    "a permission error (403), call request_approval so the user "
                    "can grant access or run it for you.",
                ]

            refs: list[tuple[ResourceType, UUID]] = []
            perms_by_ref: dict[tuple[str, UUID], set[str]] = {}
            for resource_type, resource_id, permission_id in rows:
                try:
                    ref_type = ResourceType(resource_type)
                except ValueError:
                    continue
                refs.append((ref_type, resource_id))
                perms_by_ref.setdefault((resource_type, resource_id), set()).add(
                    permission_id
                )
            names = await repo.resolve_resource_names(pod_id=pod_id, refs=refs)

        # Granted table schemas (resolve names -> column summaries).
        granted_table_names = {
            names.get((ResourceType.DATASTORE_TABLE, rid))
            for (rtype, rid) in {(r[0], r[1]) for r in rows}
            if rtype == "datastore_table"
        }
        granted_table_names.discard(None)

        # uow 2: table schema summaries (datastore read needs ctx). Render the
        # rows (lazy column access) before the uow closes.
        table_summaries: dict[str, str] = {}
        if granted_table_names:
            async with self.uow_factory() as uow:
                ctx = await create_authorization_service(uow).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                token = set_current_context(ctx)
                try:
                    tables, _ = await build_table_service(uow).list_tables(
                        pod_id, ctx, limit=_MAX_TABLES
                    )
                    for table in tables:
                        if table.table_name in granted_table_names:
                            table_summaries[table.table_name] = _table_line(table)
                finally:
                    reset_current_context(token)

        lines = [
            "\n## Granted Resources",
            "These are pre-authorized for you — read, query, and act on them "
            "directly without asking for approval. Only call request_approval if a "
            "tool returns a permission error (403), or for an explicitly "
            "destructive action.",
        ]
        for (resource_type, resource_id), perms in list(perms_by_ref.items())[
            :_MAX_RESOURCES
        ]:
            try:
                ref_type = ResourceType(resource_type)
            except ValueError:
                continue
            name = names.get((ref_type, resource_id))
            if name is None:
                continue
            perm_list = ", ".join(sorted(perms))
            if resource_type == "datastore_table" and name in table_summaries:
                lines.append(f"{table_summaries[name]}  (grants: {perm_list})")
            else:
                lines.append(f"- {resource_type}: {name}  (grants: {perm_list})")
        return lines


def _table_line(table) -> str:
    columns = ", ".join(
        f"{c.name}:{c.type.value if hasattr(c.type, 'value') else c.type}"
        for c in table.columns[:_MAX_COLUMNS]
    )
    return f"- {table.table_name} (pk: {table.primary_key_column}): {columns}"


def _top_level_file_entries(tree: object) -> list[str]:
    if not isinstance(tree, dict):
        return []
    children = tree.get("children")
    if not isinstance(children, list):
        return []
    entries: list[str] = []
    for child in children[:_MAX_RESOURCES]:
        if isinstance(child, dict):
            name = child.get("path") or child.get("name")
            kind = child.get("kind") or child.get("type")
            if name:
                entries.append(f"{name}" + (f" [{kind}]" if kind else ""))
    return entries

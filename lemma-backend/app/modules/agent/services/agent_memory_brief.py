"""The memory half of the runtime brief: what this agent already knows.

Split out of ``agent_context_brief`` because the two halves have opposite
staleness profiles. The inventory half (tables, agents, functions, files)
changes when somebody edits the pod, so a short TTL is the whole answer. The
memory half changes when the agent itself writes a fact mid-conversation, and a
TTL alone means the agent is told it does not know something it just learned —
so this half is cached separately and *invalidated* on write.

The key is ordered ``{pod}:{user}:{agent}`` for exactly that reason: a write
under ``/me`` invalidates one user across every agent, a write under
``/memory`` invalidates a whole pod, and both are a prefix of that key. Reverse
the order and neither is expressible without scanning.

Everything read here lands in the system prompt of *every* turn, so both a
per-scope and a whole-section cap apply. The budget is spent narrowest-scope
first: this agent's private notes about this user are the likeliest to be the
answer, and the pod-shared index is the one many agents write to and therefore
the one that grows.
"""

from __future__ import annotations

from uuid import UUID

from redis.exceptions import RedisError

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.composition.agent_datastore import build_file_service
from app.composition.authorization import create_authorization_service
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_memory_paths import (
    AgentMemoryPaths,
    agent_memory_paths,
)
from app.modules.agent.domain.entities import Agent
from app.modules.datastore.contracts import (
    DatastoreAccessDeniedError,
    DatastoreFileNotFoundError,
    normalize_datastore_path,
)

_CACHE_PREFIX = "agent:memory-brief"

# Every cache touch here is optional: the section rebuilds from the datastore
# on a miss, and a failed invalidation only means the entry lives out its TTL.
# So Redis being unreachable is swallowed at each call site -- narrowly, because
# a bug in the rendering above must still surface rather than read as "no cache".
_CACHE_UNAVAILABLE = (RedisError, OSError)

# Roots a write has to be under to mean anything to memory. `/me` is the API
# alias; `/{user_id}` is the same tree as the datastore stores it, and a write
# arriving from the service layer carries the raw form.
_SHARED_ROOT = "/memory"
_PERSONAL_ALIAS = "/me"

_memory_cache: RedisJsonCache | None = None


def _get_cache() -> RedisJsonCache | None:
    global _memory_cache
    ttl = agent_settings.agent_memory_brief_cache_ttl_seconds
    if ttl <= 0:
        return None
    if _memory_cache is None or _memory_cache._ttl_seconds != ttl:
        _memory_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_CACHE_PREFIX,
            ttl_seconds=ttl,
        )
    return _memory_cache


def _cache_suffix(*, pod_id: UUID, user_id: UUID, agent_id: UUID) -> str:
    return f"{pod_id}:{user_id}:{agent_id}"


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


async def invalidate_memory_brief(
    *, pod_id: UUID, path: str | None, user_id: UUID | None
) -> None:
    """Drop the cached memory sections a write to ``path`` could have changed.

    Best-effort by design, and called from more than one place because no single
    one sees every writer: the pod tools call it inline, and a stream subscriber
    covers the shell's ``lemma files write``, which reaches the datastore over
    HTTP and never enters this process. A missed call is not a correctness
    problem — the TTL still expires the entry — so Redis being unavailable is
    swallowed rather than allowed to fail somebody's write.
    """
    cache = _get_cache()
    if cache is None or not path:
        return
    normalized = normalize_datastore_path(path)
    if _is_under(normalized, _SHARED_ROOT):
        # Pod-shared: every user, every agent.
        sub_prefix = f"{pod_id}:"
    else:
        owner = _personal_owner(normalized, user_id)
        if owner is None:
            return
        # One user's private tree: that user, every agent.
        sub_prefix = f"{pod_id}:{owner}:"
    try:
        await cache.delete_prefix(sub_prefix)
    except _CACHE_UNAVAILABLE:
        # The entry expires on its TTL instead. Never let a failure to clear a
        # cache turn into a failed file write.
        pass


def _personal_owner(path: str, user_id: UUID | None) -> UUID | None:
    """Whose private tree ``path`` is in, or ``None`` if it is not in one.

    Two path forms reach this. Tools hand over the storage form,
    ``/{owner-uuid}/...``, where the owner is stated and needs no guessing --
    which matters because the actor on a write is not always the owner. The
    ``/me`` alias only appears when a caller passes what the agent typed, and
    there the requester is the owner by definition.
    """
    if _is_under(path, _PERSONAL_ALIAS):
        return user_id
    first_segment = path.lstrip("/").split("/", 1)[0]
    try:
        return UUID(first_segment)
    except ValueError:
        return None


def _marker(dropped: int, path: str) -> str:
    if dropped <= 0:
        # One line longer than the whole budget: cut inside it rather than drop
        # it, and say so honestly instead of reporting "0 more lines".
        note = f"truncated mid-line — read `{path}` for the rest"
    else:
        plural = "line" if dropped == 1 else "lines"
        note = (
            f"truncated: {dropped} more {plural} in `{path}` — read the file for them"
        )
    return f"\n… [{note}]"


def truncate_index(text: str, *, limit: int, path: str) -> str:
    """Trim an index to ``limit`` chars at a line boundary, and say so.

    The return value honours ``limit`` including its own marker, which is the
    whole point: the caller is spending a section budget, and a marker charged
    on top of the limit it was told about is a budget that does not hold. So
    room for the marker is reserved before the cut, using the longest one this
    text could produce — every actual marker is shorter, because it can never
    report more dropped lines than the file has.

    The marker names the path and the number of lines lost, because the agent is
    the only party who can fix a bloated index, and it cannot do that from
    content that simply stops. A single line longer than the budget is cut
    inside rather than dropped whole — some content beats none — and when even
    the marker will not fit, the scope is dropped entirely rather than rendered
    as a heading over nothing.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    room = limit - len(_marker(len(text.splitlines()), path))
    if room <= 0:
        return ""
    head = text[:room]
    boundary = head.rfind("\n")
    kept = (head[:boundary] if boundary > 0 else head).rstrip()
    # Counted as whole lines lost, not newlines in the remainder: the remainder
    # starts WITH the newline that ended the last kept line, so counting
    # separators overstates by one on every truncation.
    dropped = len(text.splitlines()) - len(kept.splitlines())
    return f"{kept}{_marker(dropped, path)}"


class AgentMemoryBriefBuilder:
    """Renders the ``## Your Memory`` section, cached and capped."""

    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def build(self, *, agent: Agent, pod_id: UUID, user_id: UUID) -> str:
        """The rendered section, or ``""`` when nothing is worth saying.

        Callers gate on ``memory_is_active`` before getting here; this method
        assumes the agent can act on what it is told.
        """
        suffix = _cache_suffix(pod_id=pod_id, user_id=user_id, agent_id=agent.id)
        cache = _get_cache()
        if cache is not None:
            try:
                cached = await cache.get_raw(suffix)
            except _CACHE_UNAVAILABLE:
                cached = None  # Rebuild instead; never fail a run.
            if cached is not None:
                return cached
        section = await self._render(agent=agent, pod_id=pod_id, user_id=user_id)
        if cache is not None:
            try:
                await cache.set_raw(suffix, section)
            except _CACHE_UNAVAILABLE:
                pass  # Serve this run uncached; the next one tries again.
        return section

    async def _render(self, *, agent: Agent, pod_id: UUID, user_id: UUID) -> str:
        paths = agent_memory_paths(agent)
        scopes = _scopes(paths)
        contents = await self._read_agents_mds(
            [path for _, path in scopes], pod_id=pod_id, user_id=user_id
        )
        # Names all four paths, not only the two agent-scoped ones: a live pod
        # caught an agent hedging between locations ("in case the loader picks
        # the broader file") on a blank slate, where the pod- and user-level
        # scopes have no content yet and so never render as a ``###`` block
        # below. Without their paths stated here, an agent has no way to know
        # they exist as options until it goes looking for them.
        header = (
            "\n## Your Memory\n"
            f"Your agent-scoped folders: `{paths.pod_agent_folder}/` (shared "
            f"pod-wide) and `{paths.personal_agent_folder}/` (private to this "
            "user) — write new topic files there, never a path you worked out "
            "yourself.\n\n"
            "All four AGENTS.md indexes are read into this brief automatically, "
            "every turn, together — there's nothing to pick between:\n"
            f"- `{paths.pod_index}` — pod-shared, true no matter which agent\n"
            f"- `{paths.pod_agent_index}` — this agent's own shared state, "
            "pod-wide\n"
            f"- `{paths.personal_index}` — about this user, true no matter "
            "which agent\n"
            f"- `{paths.personal_agent_index}` — this agent's own state with "
            "this user\n\n"
            "A fact about the pod itself, or about the person (name, role, "
            "preferences) — belongs in the first or third. Something specific "
            "to this agent's own ongoing work or relationship — belongs in "
            "the second or fourth."
        )
        blocks = _budgeted_blocks(scopes, contents)
        return "\n".join(
            [header, *(blocks[path] for _, path in reversed(scopes) if path in blocks)]
        )

    async def _read_agents_mds(
        self, paths: list[str], *, pod_id: UUID, user_id: UUID
    ) -> dict[str, str]:
        """Text for each path that exists and is readable; the rest are omitted.

        One uow for the whole batch — these four reads are one operation against
        one store. Each read is guarded against the two expected outcomes of an
        agent that hasn't written there yet (``DatastoreFileNotFoundError``) or
        lacks a grant on it (``DatastoreAccessDeniedError``), and against a stray
        non-UTF-8 file, so one bad path doesn't take the others down.
        ``build_user_context`` itself is deliberately left unguarded — a real
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


def _scopes(paths: AgentMemoryPaths) -> tuple[tuple[str, str], ...]:
    """(label, path) narrowest scope first — the order the budget is spent in.

    This agent's private note about this user is the likeliest to be the answer
    to what is actually being asked; the pod-shared index is the one every agent
    writes to and therefore the one that grows. When there is not room for all
    four, that is the order worth keeping.
    """
    return (
        (f"{paths.slug} + this user (private)", paths.personal_agent_index),
        ("This user (private)", paths.personal_index),
        (f"{paths.slug} (shared)", paths.pod_agent_index),
        ("Pod (shared)", paths.pod_index),
    )


def _budgeted_blocks(
    scopes: tuple[tuple[str, str], ...], contents: dict[str, str]
) -> dict[str, str]:
    """Rendered block per scope that fits, spending the section budget in order.

    The budget is charged the *rendered* length -- its heading included -- so
    the cap bounds what actually reaches the prompt rather than only the part
    of it that happens to be file content.
    """
    per_index = agent_settings.agent_memory_index_max_chars
    remaining = agent_settings.agent_memory_section_max_chars
    blocks: dict[str, str] = {}
    for label, path in scopes:
        text = contents.get(path)
        if not text:
            continue
        heading = f"\n### {label} — `{path}`\n"
        room = min(per_index, remaining - len(heading))
        if room <= 0:
            continue
        trimmed = truncate_index(text, limit=room, path=path)
        if not trimmed:
            continue
        blocks[path] = f"{heading}{trimmed}"
        remaining -= len(blocks[path])
    return blocks

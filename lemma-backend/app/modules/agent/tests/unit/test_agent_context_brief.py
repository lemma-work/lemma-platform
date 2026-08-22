"""Unit tests for AgentContextBriefBuilder connection discipline + caching.

A recording uow_factory tracks how many UoWs are open at once: the builder must
never hold more than one connection at a time (each DB read in its own short
UoW), and a second build for the same key must be served from cache without
opening any UoW.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.datastore.contracts import DatastoreFileNotFoundError
from app.modules.agent.services import agent_context_brief as brief_mod
from app.modules.agent.services.agent_context_brief import (
    AgentContextBriefBuilder,
)


class _FakeBriefCache:
    """In-process stand-in for the Redis brief cache (no Redis in unit tests)."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def get_raw(self, suffix: str) -> str | None:
        return self._d.get(suffix)

    async def set_raw(self, suffix: str, payload: str) -> None:
        self._d[suffix] = payload

    async def clear_prefix(self) -> None:
        self._d.clear()


class RecordingUoWFactory:
    """Fake uow_factory recording max concurrent open UoWs (Phase 0 scaffold)."""

    def __init__(self) -> None:
        self.opened = 0
        self._live = 0
        self.max_concurrent = 0

    def __call__(self):
        outer = self

        class _CM:
            async def __aenter__(self):
                outer.opened += 1
                outer._live += 1
                outer.max_concurrent = max(outer.max_concurrent, outer._live)
                return _FakeUoW()

            async def __aexit__(self, *exc):
                outer._live -= 1
                return False

        return _CM()


class _FakeUoW:
    session = object()

    async def commit(self):  # pragma: no cover - not exercised
        ...

    async def rollback(self):  # pragma: no cover - not exercised
        ...


class _FakeBriefRepo:
    def __init__(self, uow):
        self._uow = uow

    async def get_pod_name(self, pod_id):
        return "Acme"

    async def get_user_email(self, user_id):
        return "a@b.co"

    async def get_agent_grants(self, **kwargs):
        return []

    async def resolve_resource_names(self, **kwargs):
        return {}


class _FakeListRepo:
    def __init__(self, uow):
        self._uow = uow

    async def list_by_pod(self, *args, **kwargs):
        return ([], None)


class _FakeAuthzService:
    async def build_user_context(self, **kwargs):
        return object()


class _FakeTableService:
    async def list_tables(self, *args, **kwargs):
        return ([], None)


class _FakeFileService:
    def __init__(self, agents_md: dict[str, str] | None = None):
        self._agents_md = agents_md or {}

    async def get_directory_tree(self, *args, **kwargs):
        return {}

    async def download_file_content_by_path(self, pod_id, path, ctx):
        if path not in self._agents_md:
            raise DatastoreFileNotFoundError()
        return object(), self._agents_md[path].encode("utf-8")


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(brief_mod, "AgentContextBriefRepository", _FakeBriefRepo)
    monkeypatch.setattr(brief_mod, "AgentRepository", _FakeListRepo)
    monkeypatch.setattr(
        brief_mod,
        "create_function_repository",
        lambda uow: _FakeListRepo(uow),
    )
    monkeypatch.setattr(
        brief_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(
        brief_mod, "build_table_service", lambda uow: _FakeTableService()
    )
    # A mutable dict a test can populate before calling builder.build(...) —
    # the closure reads it live, so tests set content on the same object the
    # fixture already wired in rather than needing a second monkeypatch.
    agents_md: dict[str, str] = {}
    monkeypatch.setattr(
        brief_mod, "build_file_service", lambda uow: _FakeFileService(agents_md)
    )
    # Fake the Redis brief cache with an in-process dict (fresh per test). TTL<=0
    # disables caching exactly as in production, so the cache accessor returns None.
    fake = _FakeBriefCache()

    def _fake_get_cache():
        if brief_mod.agent_settings.agent_context_brief_cache_ttl_seconds <= 0:
            return None
        return fake

    monkeypatch.setattr(brief_mod, "_get_brief_cache", _fake_get_cache)
    yield agents_md


def _named_agent():
    return SimpleNamespace(id=uuid4(), name="agent", description=None)


def _conversation(is_pod_assistant: bool):
    return SimpleNamespace(id=uuid4(), is_pod_assistant=is_pod_assistant)


async def test_named_agent_brief_never_overlaps_uows(stubbed):
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    brief = await builder.build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
    )
    assert "# Runtime Context" in brief
    assert factory.opened >= 1
    assert factory.max_concurrent == 1


async def test_default_assistant_brief_never_overlaps_uows(stubbed):
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    await builder.build(
        agent=_named_agent(),
        conversation=_conversation(True),  # pod assistant -> full inventory path
        user_id=uuid4(),
        pod_id=uuid4(),
    )
    # inventory touches tables/agents/functions/files, each its own short UoW
    assert factory.opened >= 4
    assert factory.max_concurrent == 1


async def test_brief_is_cached_second_call_opens_no_uow(stubbed, monkeypatch):
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 60
    )
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    conv = _conversation(False)
    uid, pid = uuid4(), uuid4()

    first = await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)
    opened_after_first = factory.opened
    second = await builder.build(
        agent=agent, conversation=conv, user_id=uid, pod_id=pid
    )

    assert first == second
    assert factory.opened == opened_after_first  # cache hit: no new UoWs


async def test_a_new_conversation_reuses_the_cached_brief(stubbed, monkeypatch):
    """The reason the key dropped the conversation id.

    89.9% of production runs are the first run of their conversation, so a
    conversation-keyed brief missed on ~90% of runs and rebuilt from the
    database on the hot path. Nothing in the brief is conversation-derived, so
    the second conversation must be served the first one's brief.
    """
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 60
    )
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    uid, pid = uuid4(), uuid4()

    first = await builder.build(
        agent=agent, conversation=_conversation(False), user_id=uid, pod_id=pid
    )
    opened_after_first = factory.opened
    second = await builder.build(
        agent=agent, conversation=_conversation(False), user_id=uid, pod_id=pid
    )

    assert first == second
    assert factory.opened == opened_after_first


async def test_the_two_brief_shapes_never_share_a_cache_entry(stubbed, monkeypatch):
    """The correctness guard on dropping the conversation id.

    Whether the conversation is the pod default assistant selects between the
    full pod inventory and the agent's own grants -- two different briefs from
    the same agent, pod and user. That is the one thing the conversation
    contributes, so it has to stay in the key.
    """
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 60
    )
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    uid, pid = uuid4(), uuid4()

    granted = await builder.build(
        agent=agent, conversation=_conversation(False), user_id=uid, pod_id=pid
    )
    opened_after_first = factory.opened
    inventory = await builder.build(
        agent=agent, conversation=_conversation(True), user_id=uid, pod_id=pid
    )

    assert factory.opened > opened_after_first  # rebuilt, not served the other
    assert granted != inventory


async def test_brief_cache_disabled_with_zero_ttl(stubbed, monkeypatch):
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 0
    )
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    conv = _conversation(False)
    uid, pid = uuid4(), uuid4()

    await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)
    opened_after_first = factory.opened
    await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)

    assert factory.opened > opened_after_first  # no caching: rebuilt


async def test_memory_section_states_scoped_folders_even_with_nothing_written(
    stubbed,
):
    """The 'where to write' grounding must show up before any AGENTS.md exists.

    It's the thing that tells a brand-new agent where its own writes should
    land — waiting for content to exist first would make it useless on
    exactly the run that needs it most.
    """
    builder = AgentContextBriefBuilder(RecordingUoWFactory())
    agent = _named_agent()

    brief = await builder.build(
        agent=agent,
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
    )

    assert "## Your Memory" in brief
    assert f"/memory/agents/{agent.name}" in brief
    assert f"/me/agents/{agent.name}" in brief
    # Nothing written yet -> no per-scope content blocks.
    assert "Pod (shared) —" not in brief


async def test_memory_section_surfaces_agents_md_content(stubbed):
    """Each scope's AGENTS.md, when present, is rendered under its own label."""
    agents_md = stubbed
    agents_md["/memory/AGENTS.md"] = "- pricing: see memory/pricing.md"
    agents_md["/me/AGENTS.md"] = "- prefers async updates over calls"

    builder = AgentContextBriefBuilder(RecordingUoWFactory())
    brief = await builder.build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
    )

    assert "### Pod (shared) — `/memory/AGENTS.md`" in brief
    assert "- pricing: see memory/pricing.md" in brief
    assert "### This user (private) — `/me/AGENTS.md`" in brief
    assert "- prefers async updates over calls" in brief


async def test_memory_section_never_fails_the_brief_when_reads_error(monkeypatch):
    """A missing/ungranted AGENTS.md degrades to nothing, not a broken brief.

    Every one of these four paths raises the same way an agent's very first
    run does, before it has written anything: DatastoreFileNotFoundError.
    """

    class _ExplodingFileService:
        async def get_directory_tree(self, *args, **kwargs):
            return {}

        async def download_file_content_by_path(self, *args, **kwargs):
            raise DatastoreFileNotFoundError()

    monkeypatch.setattr(brief_mod, "AgentContextBriefRepository", _FakeBriefRepo)
    monkeypatch.setattr(brief_mod, "AgentRepository", _FakeListRepo)
    monkeypatch.setattr(
        brief_mod, "create_function_repository", lambda uow: _FakeListRepo(uow)
    )
    monkeypatch.setattr(
        brief_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(
        brief_mod, "build_table_service", lambda uow: _FakeTableService()
    )
    monkeypatch.setattr(
        brief_mod, "build_file_service", lambda uow: _ExplodingFileService()
    )
    monkeypatch.setattr(brief_mod, "_get_brief_cache", lambda: None)

    builder = AgentContextBriefBuilder(RecordingUoWFactory())
    brief = await builder.build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
    )

    assert "# Runtime Context" in brief  # the rest of the brief still built
    assert "## Your Memory" in brief
    assert "###" not in brief  # no scope's content survived the failure


async def test_lem_gets_the_same_slug_as_any_other_agent(stubbed):
    """Lem needs no special-casing: its agent.name is already 'pod_default'."""
    lem = SimpleNamespace(id=uuid4(), name=DEFAULT_POD_AGENT_NAME, description=None)
    builder = AgentContextBriefBuilder(RecordingUoWFactory())

    brief = await builder.build(
        agent=lem,
        conversation=_conversation(True),  # pod assistant
        user_id=uuid4(),
        pod_id=uuid4(),
    )

    assert "/memory/agents/pod-default/" in brief
    assert "/me/agents/pod-default/" in brief

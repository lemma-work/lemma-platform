"""Unit tests for AgentContextBriefBuilder connection discipline + caching.

A recording uow_factory tracks how many UoWs are open at once: the builder must
never hold more than one connection at a time (each DB read in its own short
UoW), and a second build for the same key must be served from cache without
opening any UoW.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.datastore.contracts import DatastoreFileNotFoundError
from app.modules.agent.services import agent_context_brief as brief_mod
from app.modules.agent.services import agent_memory_brief as memory_mod
from app.modules.agent.infrastructure.context_brief_repository import UserProfile
from app.modules.agent.services.agent_context_brief import (
    AgentContextBriefBuilder,
    _user_lines,
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

    async def get_user_profile(self, user_id):
        return UserProfile(email="a@b.co")

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
    # The memory half lives in its own module with its own cache, so it needs
    # its own stubs -- patching the brief module alone would leave the real
    # authorization service and datastore wired in behind the memory section.
    monkeypatch.setattr(
        memory_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(
        memory_mod, "build_file_service", lambda uow: _FakeFileService(agents_md)
    )
    monkeypatch.setattr(memory_mod, "_get_cache", lambda: None)
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

    Most runs are the first run of their conversation, so a conversation-keyed
    brief missed on nearly every run and rebuilt from the database on the hot
    path. Nothing in the brief is conversation-derived, so
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


async def test_the_memory_section_is_absent_without_the_memory_toolset(stubbed):
    """Memory is a capability now, not something every agent is handed.

    An agent that was never granted it should not be told it has folders to
    keep facts in.
    """
    brief = await AgentContextBriefBuilder(RecordingUoWFactory()).build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
        toolsets=[AgentToolset.POD],
    )

    assert "## Your Memory" not in brief


async def test_the_memory_section_is_absent_without_a_way_to_reach_pod_files(
    stubbed,
):
    """MEMORY carries no tools, so on its own it is a promise the agent cannot
    keep — told to write durable facts, given nothing to write with."""
    brief = await AgentContextBriefBuilder(RecordingUoWFactory()).build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
        toolsets=[AgentToolset.MEMORY, AgentToolset.WEB_SEARCH],
    )

    assert "## Your Memory" not in brief


@pytest.mark.parametrize("file_toolset", [AgentToolset.WORKSPACE_CLI, AgentToolset.POD])
async def test_memory_appears_once_the_agent_can_write_a_file(stubbed, file_toolset):
    """Either file surface is enough — the shell or the pod tools."""
    brief = await AgentContextBriefBuilder(RecordingUoWFactory()).build(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
        toolsets=[AgentToolset.MEMORY, file_toolset],
    )

    assert "## Your Memory" in brief


async def test_memory_is_not_baked_into_the_cached_inventory(stubbed, monkeypatch):
    """The two halves are cached apart, and this is why it matters.

    A fact written mid-conversation has to reach the next turn. If the memory
    section rode inside the inventory entry, a cache hit would keep serving the
    agent a brief that predates what it just learned.
    """
    monkeypatch.setattr(
        brief_mod.agent_settings, "agent_context_brief_cache_ttl_seconds", 60
    )
    rendered = ["\n## Your Memory\nfirst"]

    class _StubMemoryBuilder:
        def __init__(self, uow_factory):
            self._uow_factory = uow_factory

        async def build(self, **kwargs):
            return rendered[0]

    monkeypatch.setattr(brief_mod, "AgentMemoryBriefBuilder", _StubMemoryBuilder)
    builder = AgentContextBriefBuilder(RecordingUoWFactory())
    kwargs = dict(
        agent=_named_agent(),
        conversation=_conversation(False),
        user_id=uuid4(),
        pod_id=uuid4(),
        toolsets=[AgentToolset.MEMORY, AgentToolset.POD],
    )
    first = await builder.build(**kwargs)
    assert "first" in first

    # Second run: the inventory is served from cache, the memory is rebuilt.
    rendered[0] = "\n## Your Memory\nsecond"
    second = await builder.build(**kwargs)

    assert "second" in second
    assert "first" not in second


class TestEveryCapSaysWhatItLeftOut:
    """A silent cap is worse than a small one.

    The brief caps tables, agents, functions, files, grants and columns. All six
    were silent, so a pod's 51st table simply did not exist as far as the agent
    was concerned -- and an agent that believes a table is absent does not go
    looking for it, it tells the user there isn't one. The grants case is worse
    still: the section is headed "These are pre-authorized for you", so a
    truncated list makes the agent ask for approval it already has.
    """

    def test_nothing_is_said_when_nothing_was_dropped(self) -> None:
        from app.modules.agent.services.agent_context_brief import _more_note

        assert _more_note(shown=3, total=3, noun="tables") == []

    def test_the_count_left_out_is_named(self) -> None:
        from app.modules.agent.services.agent_context_brief import _more_note

        (line,) = _more_note(shown=50, total=137, noun="tables")

        assert "87 more tables" in line

    def test_an_unknown_total_is_not_treated_as_nothing_more(self) -> None:
        """A repository that does not count returns None. That is 'unknown',
        and it must not crash prompt assembly either."""
        from app.modules.agent.services.agent_context_brief import _more_note

        assert _more_note(shown=5, total=None, noun="agents") == []

    def test_hidden_columns_are_declared_on_the_table_line(self) -> None:
        """A column the agent cannot see is one it omits from a write and is
        then told is required, or reports to the user as not existing."""
        from types import SimpleNamespace

        from app.modules.agent.services.agent_context_brief import (
            _MAX_COLUMNS,
            _table_line,
        )

        table = SimpleNamespace(
            table_name="orders",
            primary_key_column="id",
            columns=[
                SimpleNamespace(name=f"c{index}", type="TEXT")
                for index in range(_MAX_COLUMNS + 7)
            ],
        )

        line = _table_line(table)

        assert "+7 more columns" in line

    def test_a_narrow_table_gets_no_note(self) -> None:
        from types import SimpleNamespace

        from app.modules.agent.services.agent_context_brief import _table_line

        table = SimpleNamespace(
            table_name="orders",
            primary_key_column="id",
            columns=[SimpleNamespace(name="id", type="UUID")],
        )

        assert "more columns" not in _table_line(table)

    def test_extra_top_level_files_are_declared(self) -> None:
        from app.modules.agent.services.agent_context_brief import (
            _MAX_RESOURCES,
            _top_level_file_entries,
        )

        tree = {
            "children": [
                {"path": f"/f{index}", "kind": "FILE"}
                for index in range(_MAX_RESOURCES + 4)
            ]
        }

        entries = _top_level_file_entries(tree)

        assert any("4 more top-level entries" in entry for entry in entries)


class TestTheUserLine:
    """What the brief says about the person, which is all the agent knows.

    Nothing else in the prompt carries a name or a timezone, so an omission
    here is not a thinner brief -- it is an agent that greets an email address
    and calls 09:00 UTC "this morning" to somebody eight hours away.
    """

    def test_a_name_leads_the_line_and_the_address_stays(self):
        user_id = uuid4()

        lines = _user_lines(
            UserProfile(email="ada@example.com", display_name="Ada Lovelace"),
            user_id,
        )

        assert lines[0] == f"- User: Ada Lovelace <ada@example.com> ({user_id})"

    def test_an_unnamed_user_still_reads_as_it_always_did(self):
        user_id = uuid4()

        lines = _user_lines(UserProfile(email="ada@example.com"), user_id)

        assert lines[0] == f"- User: ada@example.com ({user_id})"

    def test_a_known_timezone_is_named_with_what_to_do_about_it(self):
        lines = _user_lines(
            UserProfile(email="ada@example.com", timezone="Asia/Kolkata"), uuid4()
        )

        assert "Asia/Kolkata" in lines[1]
        assert "UTC" in lines[1]

    def test_a_missing_timezone_is_said_rather_than_left_out(self):
        """Silence reads as "the clock in front of you is theirs"."""
        lines = _user_lines(UserProfile(email="ada@example.com"), uuid4())

        assert "not set" in lines[1]
        assert "UTC" in lines[1]

"""Unit tests for AgentContextBriefBuilder connection discipline + caching.

A recording uow_factory tracks how many UoWs are open at once: the builder must
never hold more than one connection at a time (each DB read in its own short
UoW), and a second build for the same key must be served from cache without
opening any UoW.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
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
    async def get_directory_tree(self, *args, **kwargs):
        return {}


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(brief_mod, "AgentContextBriefRepository", _FakeBriefRepo)
    monkeypatch.setattr(brief_mod, "AgentRepository", _FakeListRepo)
    monkeypatch.setattr(brief_mod, "FunctionRepository", _FakeListRepo)
    monkeypatch.setattr(
        brief_mod, "create_authorization_service", lambda uow: _FakeAuthzService()
    )
    monkeypatch.setattr(brief_mod, "build_table_service", lambda uow: _FakeTableService())
    monkeypatch.setattr(brief_mod, "build_file_service", lambda uow: _FakeFileService())
    # Fake the Redis brief cache with an in-process dict (fresh per test). TTL<=0
    # disables caching exactly as in production, so the cache accessor returns None.
    fake = _FakeBriefCache()

    def _fake_get_cache():
        if settings.agent_context_brief_cache_ttl_seconds <= 0:
            return None
        return fake

    monkeypatch.setattr(brief_mod, "_get_brief_cache", _fake_get_cache)
    yield


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
    monkeypatch.setattr(settings, "agent_context_brief_cache_ttl_seconds", 60)
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    conv = _conversation(False)
    uid, pid = uuid4(), uuid4()

    first = await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)
    opened_after_first = factory.opened
    second = await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)

    assert first == second
    assert factory.opened == opened_after_first  # cache hit: no new UoWs


async def test_brief_cache_disabled_with_zero_ttl(stubbed, monkeypatch):
    monkeypatch.setattr(settings, "agent_context_brief_cache_ttl_seconds", 0)
    factory = RecordingUoWFactory()
    builder = AgentContextBriefBuilder(factory)
    agent = _named_agent()
    conv = _conversation(False)
    uid, pid = uuid4(), uuid4()

    await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)
    opened_after_first = factory.opened
    await builder.build(agent=agent, conversation=conv, user_id=uid, pod_id=pid)

    assert factory.opened > opened_after_first  # no caching: rebuilt

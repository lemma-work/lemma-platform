"""Unit tests for the session-approval store (Redis-backed, TTL-bound)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization import session_approvals


class _FakeCache:
    def __init__(self):
        self.data: dict[str, object] = {}

    async def set_json(self, suffix, value, ttl_seconds=None):
        self.data[suffix] = value

    async def get_json(self, suffix):
        return self.data.get(suffix)


class _BrokenCache:
    async def set_json(self, suffix, value, ttl_seconds=None):
        raise ConnectionError("redis down")

    async def get_json(self, suffix):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_record_then_has_session_approval_roundtrip(monkeypatch):
    cache = _FakeCache()
    monkeypatch.setattr(session_approvals, "_get_approval_cache", lambda: cache)
    session_id = str(uuid4())
    actor = f"agent:{uuid4()}"

    await session_approvals.record_session_approval(
        session_id=session_id,
        workload_actor_id=actor,
        permission_id="datastore.table.delete",
        resolved_by_user_id=uuid4(),
    )

    assert await session_approvals.has_session_approval(
        session_id=session_id,
        workload_actor_id=actor,
        permission_id="datastore.table.delete",
    )
    # Key includes the permission: a different action type stays unapproved.
    assert not await session_approvals.has_session_approval(
        session_id=session_id,
        workload_actor_id=actor,
        permission_id="folder.delete",
    )
    # Key includes the session: a different conversation stays unapproved.
    assert not await session_approvals.has_session_approval(
        session_id=str(uuid4()),
        workload_actor_id=actor,
        permission_id="datastore.table.delete",
    )
    # Key includes the workload: a different agent stays unapproved.
    assert not await session_approvals.has_session_approval(
        session_id=session_id,
        workload_actor_id=f"agent:{uuid4()}",
        permission_id="datastore.table.delete",
    )


@pytest.mark.asyncio
async def test_missing_session_or_actor_short_circuits(monkeypatch):
    def fail():
        raise AssertionError("cache must not be consulted without a session key")

    monkeypatch.setattr(session_approvals, "_get_approval_cache", fail)
    assert not await session_approvals.has_session_approval(
        session_id=None, workload_actor_id="agent:x", permission_id="pod.delete"
    )
    assert not await session_approvals.has_session_approval(
        session_id="s", workload_actor_id=None, permission_id="pod.delete"
    )


@pytest.mark.asyncio
async def test_redis_down_degrades_to_unapproved_with_warning(monkeypatch, caplog):
    monkeypatch.setattr(
        session_approvals, "_get_approval_cache", lambda: _BrokenCache()
    )
    with caplog.at_level("WARNING"):
        assert not await session_approvals.has_session_approval(
            session_id=str(uuid4()),
            workload_actor_id=f"agent:{uuid4()}",
            permission_id="datastore.table.delete",
        )
        await session_approvals.record_session_approval(
            session_id=str(uuid4()),
            workload_actor_id=f"agent:{uuid4()}",
            permission_id="datastore.table.delete",
            resolved_by_user_id=uuid4(),
        )
    assert sum("Session-approval store unavailable" in r.message for r in caplog.records) == 2


@pytest.mark.asyncio
async def test_ttl_zero_disables_session_approvals(monkeypatch):
    monkeypatch.setattr(
        session_approvals.settings, "session_approval_ttl_seconds", 0
    )
    monkeypatch.setattr(session_approvals, "_approval_cache", None)
    assert session_approvals._get_approval_cache() is None
    assert not await session_approvals.has_session_approval(
        session_id="s", workload_actor_id="agent:x", permission_id="pod.delete"
    )

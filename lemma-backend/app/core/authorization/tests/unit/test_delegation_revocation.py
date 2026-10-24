"""Delegation revocation set: revoke_delegation marks an actor, and
is_delegation_revoked reports it (degrading to not-revoked on Redis errors)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.authorization import delegation_revocation as dr


class _RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def set_json(self, suffix: str, value: object) -> None:
        self.store[suffix] = value

    async def get_json(self, suffix: str):
        return self.store.get(suffix)


class _BrokenCache:
    async def set_json(self, suffix: str, value: object) -> None:
        raise ConnectionError("redis down")

    async def get_json(self, suffix: str):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_revoke_then_check_reports_revoked(monkeypatch):
    recorder = _RecordingCache()
    monkeypatch.setattr(dr, "_get_revocation_cache", lambda: recorder)
    actor_id = uuid4()

    assert await dr.is_delegation_revoked(actor_id=actor_id) is False
    await dr.revoke_delegation(actor_id=actor_id)
    assert await dr.is_delegation_revoked(actor_id=actor_id) is True


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_not_revoked(monkeypatch):
    monkeypatch.setattr(dr, "_get_revocation_cache", lambda: _BrokenCache())
    # Neither call raises; a store outage must not fail the request.
    await dr.revoke_delegation(actor_id=uuid4())
    assert await dr.is_delegation_revoked(actor_id=uuid4()) is False


@pytest.mark.asyncio
async def test_an_ended_session_refuses_only_tokens_issued_before_its_end(
    monkeypatch,
):
    """A conversation that ends takes its tokens with it; one that resumes
    mints new ones, which its earlier end must not reach."""
    recorder = _RecordingCache()
    monkeypatch.setattr(dr, "_get_session_end_cache", lambda: recorder)

    assert await dr.delegation_session_ended(session_id="c", issued_at=100.0) is False
    await dr.end_delegation_session(session_id="c", ended_at=200.0)

    assert await dr.delegation_session_ended(session_id="c", issued_at=150.0) is True
    assert await dr.delegation_session_ended(session_id="c", issued_at=250.0) is False
    assert (
        await dr.delegation_session_ended(session_id="other", issued_at=150.0) is False
    )


@pytest.mark.asyncio
async def test_a_late_end_never_moves_an_end_earlier(monkeypatch):
    """A retried item reports the moment the conversation really ended, and
    arriving after a later end it must not undo that one."""
    recorder = _RecordingCache()
    monkeypatch.setattr(dr, "_get_session_end_cache", lambda: recorder)

    await dr.end_delegation_session(session_id="c", ended_at=300.0)
    await dr.end_delegation_session(session_id="c", ended_at=200.0)

    assert await dr.delegation_session_ended(session_id="c", issued_at=250.0) is True


@pytest.mark.asyncio
async def test_a_session_end_store_outage_degrades_to_not_ended(monkeypatch):
    monkeypatch.setattr(dr, "_get_session_end_cache", lambda: _BrokenCache())
    await dr.end_delegation_session(session_id="c")
    assert await dr.delegation_session_ended(session_id="c", issued_at=1.0) is False

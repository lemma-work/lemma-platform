"""Tests for the daemon ``mark_offline`` connected_at guard.

A reconnecting daemon bumps ``connected_at`` on the row. The previous
connection's ``finally`` block then runs and would unconditionally
mark the row OFFLINE, leaving the UI stuck on "Not detected" until
the user manually refreshes. The guard makes the mark conditional
on the row's current owner.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.repositories import AgentRuntimeDaemonRepository


class _FakeUow:
    """Stub UoW that owns a ``session`` with no flush side effects."""

    def __init__(self) -> None:
        self.session = AsyncMock()
        self.session.flush = AsyncMock(return_value=None)


def _make_repo() -> AgentRuntimeDaemonRepository:
    return AgentRuntimeDaemonRepository(_FakeUow())  # type: ignore[arg-type]


def _instance(
    *,
    status: str = "ONLINE",
    connected_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    disconnected_at: datetime | None = None,
) -> object:
    return type(
        "FakeDaemon",
        (),
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "device_key": "dev",
            "display_name": "daemon",
            "status": status,
            "device_info": {},
            "harness_catalog": {},
            "last_seen_at": last_seen_at or datetime.now(timezone.utc),
            "connected_at": connected_at,
            "disconnected_at": disconnected_at,
        },
    )()


@pytest.mark.asyncio
async def test_mark_offline_succeeds_when_connected_at_matches():
    """The owning connection drops: row should flip to OFFLINE."""
    repo = _make_repo()
    connected_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    instance = _instance(connected_at=connected_at, last_seen_at=connected_at)

    # Patch get_for_user to return our fake row, no SQL.
    repo.get_for_user = AsyncMock(return_value=instance)  # type: ignore[method-assign]

    user_id = instance.user_id  # type: ignore[attr-defined]
    result = await repo.mark_offline(
        daemon_id=instance.id,  # type: ignore[arg-type]
        user_id=user_id,
        connected_at=connected_at,
    )

    assert result is instance
    assert instance.status == "OFFLINE"
    assert instance.disconnected_at is not None


@pytest.mark.asyncio
async def test_mark_offline_no_ops_when_connected_at_diverges():
    """A newer connection has taken over: do NOT clobber it.

    This is the actual reconnect race: connection #1 drops, but
    connection #2 already bumped connected_at. The finally block of
    #1 must leave the live row alone.
    """
    repo = _make_repo()
    old_connected = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    new_connected = old_connected + timedelta(seconds=30)
    instance = _instance(
        status="ONLINE",  # newer connection already set it ONLINE
        connected_at=new_connected,
        last_seen_at=new_connected,
    )
    repo.get_for_user = AsyncMock(return_value=instance)  # type: ignore[method-assign]

    user_id = instance.user_id  # type: ignore[attr-defined]
    result = await repo.mark_offline(
        daemon_id=instance.id,  # type: ignore[arg-type]
        user_id=user_id,
        connected_at=old_connected,
    )

    assert result is instance
    assert instance.status == "ONLINE"
    assert instance.disconnected_at is None


@pytest.mark.asyncio
async def test_mark_offline_without_connected_at_falls_back_to_unconditional():
    """Legacy callers (no connected_at) get the original behavior."""
    repo = _make_repo()
    instance = _instance(connected_at=datetime.now(timezone.utc))
    repo.get_for_user = AsyncMock(return_value=instance)  # type: ignore[method-assign]

    user_id = instance.user_id  # type: ignore[attr-defined]
    result = await repo.mark_offline(
        daemon_id=instance.id,  # type: ignore[arg-type]
        user_id=user_id,
    )

    assert result is instance
    assert instance.status == "OFFLINE"


@pytest.mark.asyncio
async def test_mark_offline_handles_naive_connected_at_timestamps():
    """Real database rows may come back with naive datetimes; compare tz-naive."""
    repo = _make_repo()
    naive_connected = datetime(2026, 1, 1, 12, 0, 0)
    # Caller passes a tz-aware version of the same instant.
    aware_connected = naive_connected.replace(tzinfo=timezone.utc)
    instance = _instance(connected_at=naive_connected, last_seen_at=aware_connected)
    repo.get_for_user = AsyncMock(return_value=instance)  # type: ignore[method-assign]

    user_id = instance.user_id  # type: ignore[attr-defined]
    result = await repo.mark_offline(
        daemon_id=instance.id,  # type: ignore[arg-type]
        user_id=user_id,
        connected_at=aware_connected,
    )

    assert result is instance
    assert instance.status == "OFFLINE"

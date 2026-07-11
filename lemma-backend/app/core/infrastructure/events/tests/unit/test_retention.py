from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.infrastructure.events.retention import prune_event_delivery_records


class _Session:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def begin(self):
        return self

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(rowcount=1)


@pytest.mark.asyncio
async def test_retention_uses_one_bounded_transaction_per_category() -> None:
    sessions: list[_Session] = []

    def session_maker() -> _Session:
        session = _Session()
        sessions.append(session)
        return session

    deleted = await prune_event_delivery_records(
        session_maker,  # type: ignore[arg-type]
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )

    assert deleted == {
        "outbox_published": 1,
        "outbox_dead_letter": 1,
        "inbox_completed": 1,
        "inbox_dead_letter": 1,
    }
    assert len(sessions) == 4
    assert all(len(session.statements) == 1 for session in sessions)

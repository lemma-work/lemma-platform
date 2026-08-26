from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.domain.run_projections import StaleAgentRunRef


class _Result:
    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, object]]:
        return self.rows

    def scalars(self) -> None:
        raise AssertionError("orphan reconciliation must not hydrate agent run models")


class _Session:
    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _Result(self.rows)


class _Uow:
    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.session = _Session(rows)

    def collect_events(self, events: list[object]) -> None:
        _ = events


@pytest.mark.asyncio
async def test_stale_run_lookup_uses_identity_projection_not_runtime_payload() -> None:
    """Legacy agent_runtime JSON cannot prevent an orphan from being finalized."""
    run_id = uuid4()
    conversation_id = uuid4()
    uow = _Uow([(run_id, conversation_id)])

    stale = await ConversationRepository(uow).list_stale_active_runs(
        cutoff_seconds=600,
    )

    assert stale == [
        StaleAgentRunRef(id=run_id, conversation_id=conversation_id),
    ]
    assert uow.session.statement is not None
    sql = str(
        uow.session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    selected_columns = sql.partition(" FROM ")[0]
    assert "agent_runs.id" in selected_columns
    assert "agent_runs.conversation_id" in selected_columns
    assert "agent_runs.agent_runtime" not in selected_columns

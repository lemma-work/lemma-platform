from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.domain.run_projections import (
    StaleAgentRunRef,
    StrandedConversationRef,
)
from app.modules.test_support.mappers import configure_test_mappers


# Both tests here compile SQL, and compiling configures the mapper graph — which
# resolves relationship targets by name and fails on `Pod` unless every model
# module has been imported. See `configure_test_mappers`.
configure_test_mappers()


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


class _RowsResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self.rows


class _CapturingSession:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return _RowsResult(self.rows)


class _CapturingUow:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.session = _CapturingSession(rows)

    def collect_events(self, events: list[object]) -> None:
        _ = events


@pytest.mark.asyncio
async def test_stranded_conversation_lookup_asks_about_the_newest_run() -> None:
    """The half `list_stale_active_runs` cannot see.

    A conversation left active by a run that already finished is invisible to a
    sweep keyed on run status, so this asks the opposite question. Compiled
    against the Postgres dialect because the LATERAL that picks the newest run
    per conversation is dialect-specific, and a query that will not compile is a
    cron logging an error every ten minutes while repairing nothing.
    """
    conversation_id = uuid4()
    uow = _CapturingUow([(conversation_id, "COMPLETED")])

    stranded = await ConversationRepository(
        uow
    ).list_conversations_stranded_by_a_finished_run(cutoff_seconds=3600)

    assert stranded == [
        StrandedConversationRef(id=conversation_id, run_status="COMPLETED")
    ]
    assert uow.session.statement is not None
    sql = str(
        uow.session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    # The newest run per conversation, not merely "some terminal run" — and
    # driven from the conversation side, so the cost does not grow with the
    # size of `agent_runs` the way a DISTINCT ON over the whole table would.
    assert "JOIN LATERAL" in sql
    assert "LIMIT 1" in sql
    # WAITING is a resting state, not a stranded one, and must never be swept:
    # collapsing it would tear down the pause an approval is waiting in.
    assert "WAITING" not in sql
    assert "RUNNING" in sql and "STOP_REQUESTED" in sql


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

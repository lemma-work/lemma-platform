"""The conversation read path must not re-read the transcript.

The service tests around retryability mock the repository, so they keep passing
whatever SQL it emits. These compile the statements instead. The failure being
guarded against is specific: ``get_conversation(include_runs=True)`` used to
eager-load every run of the conversation and every message of every run — on
the worst production thread, 13,688 messages — to derive four scalars and one
boolean.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.test_support.mappers import configure_test_mappers

# Compiling a statement configures the mappers, and a partial model graph fails
# to resolve its relationship targets by name — so without this the file passes
# in a suite and fails on its own.
configure_test_mappers()


class _Result:
    def __init__(self, rows=(), scalar_one=None) -> None:
        self._rows = list(rows)
        self._scalar_one = scalar_one

    def scalar_one_or_none(self):
        return self._scalar_one

    def scalars(self):
        return iter(self._rows)

    def one(self):
        return self._rows[0]


class _Session:
    def __init__(self, rows=(), scalar_one=None) -> None:
        self._rows = rows
        self._scalar_one = scalar_one
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self._rows, self._scalar_one)

    async def scalar(self, statement):
        self.statements.append(statement)
        return None


class _Uow:
    def __init__(self, rows=(), scalar_one=None) -> None:
        self.session = _Session(rows, scalar_one)

    def collect_events(self, events) -> None:
        _ = events


def _conversation_model():
    """A detached model, enough for ``to_entity()`` to run."""
    from app.modules.agent.infrastructure.models import ConversationModel

    model = ConversationModel()
    model.id = uuid4()
    model.user_id = uuid4()
    model.pod_id = uuid4()
    model.organization_id = uuid4()
    model.agent_id = None
    model.title = None
    model.instructions = None
    model.agent_runtime = None
    model.origin_type = None
    model.origin_id = None
    model.parent_id = None
    model.conversation_type = None
    model.status = None
    model.output_data = None
    model.conversation_metadata = None
    model.created_at = datetime(2026, 8, 14, tzinfo=timezone.utc)
    model.updated_at = datetime(2026, 8, 14, tzinfo=timezone.utc)
    return model


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


@pytest.mark.asyncio
async def test_conversation_detail_never_joins_the_message_table() -> None:
    uow = _Uow(scalar_one=_conversation_model())

    await ConversationRepository(uow).get_conversation(uuid4(), include_runs=True)

    emitted = [_sql(statement) for statement in uow.session.statements]
    assert emitted, "expected the conversation lookup to run"
    assert not any("agent_messages" in sql for sql in emitted), (
        "include_runs must not pull the transcript; it exists to describe the "
        "latest run only"
    )


@pytest.mark.asyncio
async def test_conversation_detail_asks_for_one_run_not_the_collection() -> None:
    uow = _Uow(scalar_one=_conversation_model())

    await ConversationRepository(uow).get_conversation(uuid4(), include_runs=True)

    run_queries = [
        sql for sql in map(_sql, uow.session.statements) if "agent_runs" in sql
    ]
    assert len(run_queries) == 1
    assert "limit 1" in run_queries[0]
    assert "order by" in run_queries[0]


@pytest.mark.asyncio
async def test_omitting_runs_costs_no_extra_query() -> None:
    uow = _Uow(scalar_one=_conversation_model())

    await ConversationRepository(uow).get_conversation(uuid4())

    assert not any("agent_runs" in _sql(s) for s in uow.session.statements)


@pytest.mark.asyncio
async def test_retryability_is_one_aggregate_over_a_single_run() -> None:
    """Counting in the database is what replaced loading the run's messages."""
    run_id = uuid4()

    class _CountRow:
        total = 2
        non_user = 0

    uow = _Uow([_CountRow()])

    assert await ConversationRepository(uow).run_has_only_user_messages(run_id) is True

    sql = _sql(uow.session.statements[0])
    assert "count(" in sql
    assert str(run_id) in sql
    # Aggregate only — no message bodies cross the wire.
    selected = sql.partition(" \nfrom ")[0]
    assert "agent_messages.text" not in selected
    assert "tool_result" not in selected


@pytest.mark.asyncio
async def test_a_run_with_a_reply_is_not_replay_safe() -> None:
    class _CountRow:
        total = 3
        non_user = 1

    uow = _Uow([_CountRow()])

    assert await ConversationRepository(uow).run_has_only_user_messages(uuid4()) is False


@pytest.mark.asyncio
async def test_a_run_with_no_messages_is_not_replay_safe() -> None:
    """Nothing to replay is a different thing from safe to replay."""

    class _CountRow:
        total = 0
        non_user = 0

    uow = _Uow([_CountRow()])

    assert await ConversationRepository(uow).run_has_only_user_messages(uuid4()) is False


@pytest.mark.asyncio
async def test_child_listing_resolves_latest_runs_in_one_statement() -> None:
    """N children must not become N run lookups, nor one collection-wide load."""
    uow = _Uow()

    await ConversationRepository(uow).list_children(
        parent_id=uuid4(), user_id=uuid4(), limit=50
    )

    # No children came back, so no second query should have been needed at all.
    assert not any("agent_runs" in _sql(s) for s in uow.session.statements)

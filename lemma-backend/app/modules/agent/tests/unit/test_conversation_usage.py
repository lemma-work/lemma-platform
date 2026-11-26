"""What a conversation has cost, read back by the person it belongs to.

Three things are pinned here. The summary: a run with nothing recorded is
"not reported", never $0, and the totals say how many such runs they leave
out. Who may read it: exactly whoever may read the conversation, which is its
owner, and nobody gets as far as the ledger otherwise. And the ledger reads:
only rows recorded against the reader, so the route shows a subset of what
``/usage/me/events`` already shows them.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.core.authorization.current import reset_current_context, set_current_context
from app.modules.agent.api.run_schemas import ConversationUsageResponse
from app.modules.agent.application.conversations.read import (
    ConversationLedger,
    ConversationReads,
)
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    ConversationType,
)
from app.modules.agent.infrastructure.db.runs import RunWindow
from app.modules.test_support.authz import allow_all_context
from app.modules.usage.contracts.run_usage import LedgerTotals

POD_ID = uuid4()
STARTED = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)


def _window(
    status: AgentRunStatus = AgentRunStatus.COMPLETED, *, minutes: int = 0
) -> RunWindow:
    started = STARTED + timedelta(minutes=minutes)
    return RunWindow(
        id=uuid4(),
        status=status,
        started_at=started,
        finished_at=None
        if status is AgentRunStatus.RUNNING
        else started + timedelta(minutes=1),
    )


def _totals(cost: float, input_tokens: int, output_tokens: int) -> LedgerTotals:
    return LedgerTotals(
        cost_usd=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=input_tokens // 2,
        model_calls=2,
    )


# --- the summary ---


def test_a_run_with_nothing_recorded_reads_not_reported_rather_than_free():
    running, reported, silent = (
        _window(AgentRunStatus.RUNNING, minutes=2),
        _window(minutes=1),
        _window(),
    )
    totals = _totals(0.42, 1200, 300)
    ledger = ConversationLedger(
        windows=[running, reported, silent],
        by_run={reported.id: totals},
        totals=totals,
        run_count=3,
        reported_run_count=1,
    )
    conversation_id = uuid4()

    response = ConversationUsageResponse.of(conversation_id, ledger)

    assert response.conversation_id == conversation_id
    assert response.totals == totals
    assert response.run_count == 3
    assert response.unreported_run_count == 2
    assert response.has_more_runs is False
    assert [run.agent_run_id for run in response.runs] == [
        running.id,
        reported.id,
        silent.id,
    ]
    by_id = {run.agent_run_id: run for run in response.runs}
    assert by_id[reported.id].usage == totals
    assert by_id[silent.id].usage is None
    assert by_id[running.id].usage is None
    assert by_id[running.id].finished_at is None
    assert by_id[running.id].status is AgentRunStatus.RUNNING


def test_totals_cover_runs_older_than_the_ones_listed():
    newest = _window()
    totals = _totals(3.5, 90_000, 4_000)
    ledger = ConversationLedger(
        windows=[newest],
        by_run={newest.id: _totals(0.5, 1000, 100)},
        totals=totals,
        run_count=250,
        reported_run_count=250,
    )

    response = ConversationUsageResponse.of(uuid4(), ledger)

    assert response.has_more_runs is True
    assert response.unreported_run_count == 0
    assert response.totals.cost_usd == 3.5
    assert response.runs[0].usage is not None
    assert response.runs[0].usage.cost_usd == 0.5


def test_a_run_started_between_the_two_counts_never_reads_negative():
    ledger = ConversationLedger(
        windows=[],
        by_run={},
        totals=LedgerTotals(),
        run_count=1,
        reported_run_count=2,
    )

    response = ConversationUsageResponse.of(uuid4(), ledger)

    assert response.unreported_run_count == 0


def test_a_conversation_with_no_runs_reads_zero_with_nothing_missing():
    response = ConversationUsageResponse.of(
        uuid4(),
        ConversationLedger(
            windows=[],
            by_run={},
            totals=LedgerTotals(),
            run_count=0,
            reported_run_count=0,
        ),
    )

    assert response.totals == LedgerTotals()
    assert response.runs == []
    assert response.unreported_run_count == 0
    assert response.has_more_runs is False


# --- who may read it, and what the ledger is asked ---


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows

    def one(self) -> tuple[object, ...]:
        (row,) = self._rows
        return row


class _Session:
    """Answers each read in the order ``get_usage`` makes it, and keeps every
    statement to compile."""

    def __init__(self, answers: list[object]) -> None:
        self.answers = answers
        self.statements: list[Select] = []

    async def execute(self, statement: Select) -> _Rows:
        self.statements.append(statement)
        answer = self.answers.pop(0)
        assert isinstance(answer, list)
        return _Rows(answer)

    async def scalar(self, statement: Select) -> object:
        self.statements.append(statement)
        return self.answers.pop(0)


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session


class _Conversations:
    def __init__(self, conversation: Conversation) -> None:
        self.conversation = conversation

    async def get_conversation(
        self, conversation_id: UUID, *, include_runs: bool = False
    ) -> Conversation | None:
        return self.conversation if conversation_id == self.conversation.id else None


@pytest.fixture
def owner_id() -> Iterator[UUID]:
    user_id = uuid4()
    token = set_current_context(allow_all_context(user_id=user_id, pod_id=POD_ID))
    yield user_id
    reset_current_context(token)


def _conversation(owner: UUID) -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=POD_ID,
        user_id=owner,
        agent_id=uuid4(),
        title="coding",
        type=ConversationType.CHAT,
        status=ConversationStatus.COMPLETED,
    )


def _reads(conversation: Conversation, session: _Session) -> ConversationReads:
    """The real reads and the real ``Runs``, over a session that answers from a
    script; only the conversation's row is handed in."""
    reads = ConversationReads(_Uow(session), None)  # type: ignore[arg-type]
    reads.conversations = _Conversations(conversation)  # type: ignore[assignment]
    return reads


def _sql(statement: Select) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.asyncio
async def test_the_owner_reads_their_conversations_cost_from_their_own_rows(
    owner_id: UUID,
):
    conversation = _conversation(owner_id)
    reported, silent = _window(minutes=1), _window()
    session = _Session(
        [
            # The newest runs.
            [
                (run.id, run.status.value, run.started_at, run.finished_at)
                for run in (reported, silent)
            ],
            # Every run: cost, input, output, cached, cache writes, calls, runs.
            [(0.75, 1500, 200, 600, 0, 3, 1)],
            # The listed runs, one row per run with anything recorded.
            [(reported.id, 0.75, 1500, 200, 600, 0, 3)],
            # How many runs the conversation has.
            2,
        ]
    )

    ledger = await _reads(conversation, session).get_usage(
        conversation_id=conversation.id,
        user_id=owner_id,
        pod_id=POD_ID,
        run_limit=200,
    )
    response = ConversationUsageResponse.of(conversation.id, ledger)

    assert response.totals == LedgerTotals(
        cost_usd=0.75,
        input_tokens=1500,
        output_tokens=200,
        cached_input_tokens=600,
        model_calls=3,
    )
    assert response.run_count == 2
    assert response.unreported_run_count == 1
    assert [run.agent_run_id for run in response.runs] == [reported.id, silent.id]
    assert [run.usage is not None for run in response.runs] == [True, False]

    windows_sql, totals_sql, by_run_sql, count_sql = (
        _sql(statement) for statement in session.statements
    )
    assert "LIMIT 200" in windows_sql
    assert f"agent_runs.conversation_id = '{conversation.id}'" in count_sql
    # Held to the reader's own rows, in both ledger reads.
    assert f"usage_records.user_id = '{owner_id}'" in totals_sql
    assert f"usage_records.user_id = '{owner_id}'" in by_run_sql
    # Every run of this conversation, nested rather than listed.
    assert "usage_records.agent_run_id IN (SELECT agent_runs.id" in totals_sql
    assert f"agent_runs.conversation_id = '{conversation.id}'" in totals_sql
    # One row per listed run, and only the listed runs.
    assert "GROUP BY usage_records.agent_run_id" in by_run_sql
    assert str(reported.id) in by_run_sql and str(silent.id) in by_run_sql
    # Cost over every kind of row; tokens and calls over model calls only.
    assert "sum(coalesce(usage_records.cost_amount" in totals_sql
    assert "FILTER (WHERE lower(usage_records.usage_kind) = 'llm')" in totals_sql


@pytest.mark.asyncio
async def test_someone_else_cannot_read_a_conversations_cost(owner_id: UUID):
    conversation = _conversation(owner=uuid4())
    session = _Session([])

    with pytest.raises(ConversationNotFoundError):
        await _reads(conversation, session).get_usage(
            conversation_id=conversation.id,
            user_id=owner_id,
            pod_id=POD_ID,
            run_limit=200,
        )

    assert session.statements == [], "refused before the ledger is asked"


@pytest.mark.asyncio
async def test_a_conversation_in_another_pod_is_not_found(owner_id: UUID):
    conversation = _conversation(owner_id)
    session = _Session([])

    with pytest.raises(ConversationNotFoundError):
        await _reads(conversation, session).get_usage(
            conversation_id=conversation.id,
            user_id=owner_id,
            pod_id=uuid4(),
            run_limit=200,
        )

    assert session.statements == []

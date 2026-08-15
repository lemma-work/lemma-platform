"""The bounded history loader, against a real PostgreSQL.

The unit tests cover the selection policy -- that loading only what gets sent
picks the same messages as loading everything. They cannot cover the SQL that
produces that shape: the per-run counts, and the two ``DISTINCT ON`` reads that
pull each older run's first and last message. Those are the parts that can be
silently wrong (or silently unbounded) and still pass a test built on fakes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository

pytestmark = [pytest.mark.e2e]

_BASE = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
_FULL_RUNS = 5


async def _seed(
    db_session, scenario, *, runs: int, messages_per_run: int
) -> list[UUID]:
    """One conversation with `runs` runs, each holding `messages_per_run`."""
    conversation = ConversationModel(
        id=uuid4(),
        user_id=UUID(scenario.owner_user["id"]),
        pod_id=UUID(scenario.pod_id),
        organization_id=UUID(scenario.org_id),
    )
    db_session.add(conversation)
    run_ids: list[UUID] = []
    for run_index in range(runs):
        run = AgentRunModel(
            id=uuid4(),
            conversation_id=conversation.id,
            status="COMPLETED",
            agent_runtime={"profile_id": "system:lemma"},
            started_at=_BASE + timedelta(minutes=run_index),
            created_at=_BASE + timedelta(minutes=run_index),
        )
        db_session.add(run)
        run_ids.append(run.id)
        for index in range(messages_per_run):
            db_session.add(
                MessageModel(
                    id=uuid4(),
                    conversation_id=conversation.id,
                    agent_run_id=run.id,
                    sequence=(run_index * 1000) + index,
                    role="user" if index == 0 else "assistant",
                    kind="TEXT",
                    text=f"run {run_index} message {index}",
                    created_at=_BASE + timedelta(minutes=run_index, seconds=index),
                )
            )
    await db_session.flush()
    return run_ids


def _repo(db_session) -> ConversationRepository:
    return ConversationRepository(SqlAlchemyUnitOfWork(db_session))


async def test_older_runs_come_back_as_first_and_last_with_a_true_count(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="History")
    run_ids = await _seed(db_session, scenario, runs=9, messages_per_run=7)

    runs = await _repo(db_session).load_runtime_history_by_run_id(
        run_ids[-1], full_run_count=_FULL_RUNS
    )

    assert [run.id for run in runs] == run_ids  # chronological, all runs present
    for run in runs[-_FULL_RUNS:]:
        assert len(run.messages) == 7
        assert run.message_count == 7
    for run in runs[:-_FULL_RUNS]:
        assert len(run.messages) == 2  # first and last only
        assert run.message_count == 7  # but it still knows there were seven
        assert run.messages[0].text.endswith("message 0")
        assert run.messages[-1].text.endswith("message 6")


async def test_the_load_does_not_grow_with_conversation_length(db_session, scenario):
    """The property the whole change exists for."""
    await scenario.create_org_with_pod(name_prefix="HistoryLen")
    short_ids = await _seed(db_session, scenario, runs=6, messages_per_run=4)
    long_ids = await _seed(db_session, scenario, runs=60, messages_per_run=40)
    repo = _repo(db_session)

    short = await repo.load_runtime_history_by_run_id(
        short_ids[-1], full_run_count=_FULL_RUNS
    )
    long = await repo.load_runtime_history_by_run_id(
        long_ids[-1], full_run_count=_FULL_RUNS
    )

    # 60 runs x 40 messages is 2400 rows; the old loader returned every one.
    assert sum(len(run.messages) for run in long) == (_FULL_RUNS * 40) + (55 * 2)
    assert sum(len(run.messages) for run in short) == (_FULL_RUNS * 4) + (1 * 2)


async def test_runs_at_the_elision_boundary_survive_intact(db_session, scenario):
    """A one-message run is its own first and last; it must appear once."""
    await scenario.create_org_with_pod(name_prefix="HistoryEdge")
    run_ids = await _seed(db_session, scenario, runs=8, messages_per_run=1)

    runs = await _repo(db_session).load_runtime_history_by_run_id(
        run_ids[-1], full_run_count=_FULL_RUNS
    )

    for run in runs:
        assert len(run.messages) == 1
        assert run.message_count == 1


async def test_a_run_with_no_messages_is_still_returned(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="HistoryEmpty")
    run_ids = await _seed(db_session, scenario, runs=7, messages_per_run=0)

    runs = await _repo(db_session).load_runtime_history_by_run_id(
        run_ids[-1], full_run_count=_FULL_RUNS
    )

    assert len(runs) == 7
    assert all(run.messages == [] and run.message_count == 0 for run in runs)


async def test_an_unknown_run_loads_nothing(db_session):
    loaded = await _repo(db_session).load_runtime_history_by_run_id(
        uuid4(), full_run_count=_FULL_RUNS
    )
    assert loaded == []

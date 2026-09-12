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
from app.modules.agent.services.runtime_history import (
    MAX_HISTORY_AGENT_RUNS,
    runtime_full_run_ids,
)
from app.modules.test_support.query_counting import counted_queries

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


async def _window(db_session, agent_run_id, *, limit=MAX_HISTORY_AGENT_RUNS):
    return await _repo(db_session).load_runtime_history_digests_by_run_id(
        agent_run_id, limit=limit
    )


async def _load(db_session, agent_run_id, *, limit=MAX_HISTORY_AGENT_RUNS):
    """The two-phase load, as the runner performs it.

    Digests first, then the trim decides which runs need every message, then the
    messages are attached. Loading in one pass and choosing by position is the
    defect this mirrors around -- see the unit equivalence tests.
    """
    repo = _repo(db_session)
    window = await _window(db_session, agent_run_id, limit=limit)
    if not window.runs:
        return window.runs
    return await repo.attach_runtime_history_messages(
        window.runs, full_run_ids=runtime_full_run_ids(window.runs, None)
    )


async def test_older_runs_come_back_as_first_and_last_with_a_true_count(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="History")
    run_ids = await _seed(db_session, scenario, runs=9, messages_per_run=7)

    runs = await _load(db_session, run_ids[-1])

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
    short = await _load(db_session, short_ids[-1])
    long = await _load(db_session, long_ids[-1])

    # 60 runs x 40 messages is 2400 rows; the old loader returned every one.
    assert sum(len(run.messages) for run in long) == (_FULL_RUNS * 40) + (55 * 2)
    assert sum(len(run.messages) for run in short) == (_FULL_RUNS * 4) + (1 * 2)


async def test_runs_at_the_elision_boundary_survive_intact(db_session, scenario):
    """A one-message run is its own first and last; it must appear once."""
    await scenario.create_org_with_pod(name_prefix="HistoryEdge")
    run_ids = await _seed(db_session, scenario, runs=8, messages_per_run=1)

    runs = await _load(db_session, run_ids[-1])

    for run in runs:
        assert len(run.messages) == 1
        assert run.message_count == 1


async def test_a_run_with_no_messages_is_still_returned(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="HistoryEmpty")
    run_ids = await _seed(db_session, scenario, runs=7, messages_per_run=0)

    runs = await _load(db_session, run_ids[-1])

    assert len(runs) == 7
    assert all(run.messages == [] and run.message_count == 0 for run in runs)


async def test_an_unknown_run_loads_nothing(db_session):
    assert await _load(db_session, uuid4()) == []


async def test_the_window_stops_at_the_runs_a_prompt_can_carry(db_session, scenario):
    """The cap used to be `runs[-60:]`, applied to every run ever loaded.

    A four-hundred-turn conversation read four hundred runs, and a `GROUP BY`
    over every message in all of them, to send sixty. Asserted on rows returned
    rather than on time, because that is what grew.
    """
    await scenario.create_org_with_pod(name_prefix="HistoryWindow")
    run_ids = await _seed(db_session, scenario, runs=12, messages_per_run=2)

    window = await _window(db_session, run_ids[-1], limit=5)

    assert [run.id for run in window.runs] == run_ids[-5:], (
        "the window must take the newest runs, and hand them back oldest-first"
    )
    assert window.total_runs == 12
    assert window.dropped_by_window == 7


async def test_the_total_survives_the_window(db_session, scenario):
    """The elision notice is built from how many runs were removed.

    A windowed list cannot know what it was cut from, so without the count the
    notice under-reports by exactly the runs the window took -- and it does so
    silently, because a smaller number is still a plausible one.
    """
    await scenario.create_org_with_pod(name_prefix="HistoryTotal")
    run_ids = await _seed(db_session, scenario, runs=9, messages_per_run=3)

    windowed = await _window(db_session, run_ids[-1], limit=4)
    whole = await _window(db_session, run_ids[-1], limit=100)

    assert windowed.total_runs == whole.total_runs == 9
    assert len(windowed.runs) == 4
    assert len(whole.runs) == 9


async def test_a_run_older_than_the_window_is_still_the_run_being_executed(
    db_session, scenario
):
    """The trap the window sets, and the one that fails a run rather than misleads.

    The runner finds the run it was asked to execute in the list it loaded and
    refuses the request when it is absent. Windowing alone therefore turns a
    resumed older run in a long conversation from a missing history notice into
    `ConversationNotFoundError` -- so the resumed run comes back whether or not
    it survived the window.
    """
    await scenario.create_org_with_pod(name_prefix="HistoryResume")
    run_ids = await _seed(db_session, scenario, runs=10, messages_per_run=3)
    oldest = run_ids[0]

    window = await _window(db_session, oldest, limit=3)

    assert oldest not in {run.id for run in window.runs}, "the premise of the test"
    assert window.current_run is not None
    assert window.current_run.id == oldest
    assert window.current_run.message_count == 3, (
        "a run fetched outside the window still needs its digest"
    )


async def test_the_digest_read_does_not_grow_with_the_conversation(
    db_session, scenario
):
    """Whatever the conversation's length, the same statements run.

    The differential shape: measure at two sizes and assert the count did not
    move. An absolute budget would pass a version that reads one extra row per
    run, because rows are not statements -- so the row assertion above is the
    other half of this.
    """
    await scenario.create_org_with_pod(name_prefix="HistoryCost")
    short_ids = await _seed(db_session, scenario, runs=6, messages_per_run=2)
    long_ids = await _seed(db_session, scenario, runs=90, messages_per_run=2)

    with counted_queries() as short_statements:
        short = await _window(db_session, short_ids[-1])
    with counted_queries() as long_statements:
        long = await _window(db_session, long_ids[-1])

    assert len(short_statements) == len(long_statements), (
        f"{len(short_statements)} statements for 6 runs, {len(long_statements)} for 90"
    )
    assert len(short.runs) == 6
    assert len(long.runs) == MAX_HISTORY_AGENT_RUNS
    assert long.total_runs == 90

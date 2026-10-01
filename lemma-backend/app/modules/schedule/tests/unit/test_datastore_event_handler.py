from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.infrastructure.db.session_uow import SESSION_UOW_KEY
from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.schedule.domain.errors import ScheduleFilterUndecidedError
from app.modules.schedule.domain.interfaces import ScheduleFilterVerdict
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.services.datastore_event_handler import DatastoreEventHandler
from app.modules.schedule.services.schedule_processor import ScheduleEventOutcome
from app.modules.schedule.tests.fakes import FakeFilterOutcomes

FIRED = ScheduleEventOutcome(fired=True)


@pytest.mark.asyncio
async def test_datastore_event_handler_processes_matching_triggers():
    repo = AsyncMock()
    processor = AsyncMock()

    schedule = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "users", "operations": ["INSERT"]},
    )
    repo.find_by_pod_table_event.return_value = [schedule]
    processor.process_event.return_value = FIRED

    handler = DatastoreEventHandler(
        schedule_repository=repo,
        schedule_processor=processor,
    )

    row_owner_id = uuid4()
    event = DatastoreRecordEvent.create(
        pod_id=schedule.pod_id,
        table_name="users",
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1"},
        actor_id=schedule.user_id,
        owner_user_id=row_owner_id,
    )

    result = await handler.handle_datastore_event(event)

    assert result == [schedule.id]
    processor.process_event.assert_awaited_once()
    assert processor.process_event.await_args.kwargs["user_id"] == row_owner_id


@pytest.mark.asyncio
async def test_datastore_event_handler_uses_schedule_owner_for_shared_rows():
    repo = AsyncMock()
    processor = AsyncMock()
    schedule = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "shared", "operations": ["INSERT"]},
    )
    repo.find_by_pod_table_event.return_value = [schedule]
    processor.process_event.return_value = FIRED
    handler = DatastoreEventHandler(repo, processor)
    event = DatastoreRecordEvent.create(
        pod_id=schedule.pod_id,
        table_name="shared",
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1"},
        actor_id=uuid4(),
        owner_user_id=None,
    )

    await handler.handle_datastore_event(event)

    assert processor.process_event.await_args.kwargs["user_id"] == schedule.user_id


def _conditional_schedule(when: dict, operations: list[str] | None = None):
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={
            "table_name": "tickets",
            "operations": operations or ["UPDATE"],
            "when": when,
        },
    )


def _update_event(schedule, payload, changed, previous):
    return DatastoreRecordEvent.create(
        pod_id=schedule.pod_id,
        table_name="tickets",
        record_id="rec_1",
        operation=DatastoreRecordOperation.UPDATE,
        payload=payload,
        changed=changed,
        previous=previous,
        actor_id=schedule.user_id,
    )


@pytest.mark.asyncio
async def test_unmatched_condition_never_reaches_the_processor():
    """The point of a condition is to spend nothing — no run, and no LLM call."""
    repo = AsyncMock()
    processor = AsyncMock()
    schedule = _conditional_schedule({"status": {"to": "approved"}})
    repo.find_by_pod_table_event.return_value = [schedule]

    handler = DatastoreEventHandler(repo, processor)
    result = await handler.handle_datastore_event(
        _update_event(
            schedule,
            payload={"status": "approved"},
            changed=["status"],
            previous={"status": "approved"},  # already approved: not a transition
        )
    )

    assert result == []
    processor.process_event.assert_not_called()
    assert repo.record_fire.await_args.kwargs["status"] == ScheduleFireStatus.FILTERED


@pytest.mark.asyncio
async def test_matched_condition_fires_the_schedule():
    repo = AsyncMock()
    processor = AsyncMock()
    processor.process_event.return_value = FIRED
    schedule = _conditional_schedule({"status": {"to": "approved"}})
    repo.find_by_pod_table_event.return_value = [schedule]

    handler = DatastoreEventHandler(repo, processor)
    result = await handler.handle_datastore_event(
        _update_event(
            schedule,
            payload={"status": "approved"},
            changed=["status"],
            previous={"status": "pending"},
        )
    )

    assert result == [schedule.id]
    processor.process_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_one_filtered_schedule_does_not_hold_back_another():
    repo = AsyncMock()
    processor = AsyncMock()
    processor.process_event.return_value = FIRED
    filtered = _conditional_schedule({"status": {"to": "rejected"}})
    firing = _conditional_schedule({"status": {"to": "approved"}})
    firing.pod_id = filtered.pod_id
    repo.find_by_pod_table_event.return_value = [filtered, firing]

    handler = DatastoreEventHandler(repo, processor)
    result = await handler.handle_datastore_event(
        _update_event(
            filtered,
            payload={"status": "approved"},
            changed=["status"],
            previous={"status": "pending"},
        )
    )

    assert result == [firing.id]


@pytest.mark.asyncio
async def test_what_the_write_did_reaches_the_run_metadata():
    """A workflow should be able to read the change, not just the row."""
    repo = AsyncMock()
    processor = AsyncMock()
    processor.process_event.return_value = FIRED
    schedule = _conditional_schedule({}, operations=["UPDATE"])
    schedule.config = {"table_name": "tickets", "operations": ["UPDATE"]}
    repo.find_by_pod_table_event.return_value = [schedule]

    handler = DatastoreEventHandler(repo, processor)
    await handler.handle_datastore_event(
        _update_event(
            schedule,
            payload={"status": "approved"},
            changed=["status"],
            previous={"status": "pending"},
        )
    )

    metadata = processor.process_event.await_args.kwargs["metadata"]
    assert metadata["changed"] == ["status"]
    assert metadata["previous"] == {"status": "pending"}


@pytest.mark.asyncio
async def test_datastore_event_handler_returns_empty_when_no_matches():
    repo = AsyncMock()
    processor = AsyncMock()
    repo.find_by_pod_table_event.return_value = []

    handler = DatastoreEventHandler(
        schedule_repository=repo,
        schedule_processor=processor,
    )

    event = DatastoreRecordEvent.create(
        pod_id=uuid4(),
        table_name="users",
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1"},
        actor_id=uuid4(),
    )

    result = await handler.handle_datastore_event(event)

    assert result == []
    processor.process_event.assert_not_called()


class _Journal:
    """What happened, in the order it happened."""

    def __init__(self) -> None:
        self.entries: list[str] = []

    def note(self, name: str, returns: object = None):
        async def _record(*_args, **_kwargs):
            self.entries.append(name)
            return returns

        return _record


class _Uow:
    """A unit of work that records its commits, reachable the production way."""

    def __init__(self, journal: _Journal) -> None:
        self._journal = journal

    async def commit(self) -> None:
        self._journal.entries.append("commit")

    def after_commit(self, callback) -> None:  # pragma: no cover - unused here
        raise AssertionError("this path defers nothing")


def _repository_on(journal: _Journal) -> AsyncMock:
    """A schedule repository whose session carries a real unit of work.

    An `AsyncMock` alone is not enough: `active_uow` reads `session.info` and
    wants a genuine mapping, so a bare mock silently answers "no unit of work"
    and the release under test never runs. That is exactly how the previous
    `release=` callback went untested -- every caller here omitted it.
    """
    repo = AsyncMock()
    repo.session = SimpleNamespace(info={SESSION_UOW_KEY: _Uow(journal)})
    repo.record_fire.side_effect = journal.note("record_fire")
    return repo


@pytest.mark.asyncio
async def test_the_connection_is_handed_back_before_each_schedule_is_processed():
    """Per iteration, not once before the loop.

    A schedule carrying a `filter_instruction` runs an LLM inference inline, so
    holding the transaction across it keeps a pooled connection idle for the
    length of every call in the loop. One release before the loop would not do:
    the FILTERED/TRIGGERED fire row written for schedule N re-dirties the
    session before schedule N+1's inference.

    The property is an ordering, so the assertion is on the sequence. A commit
    *count* would pass just as well with the commit in the wrong place.
    """
    journal = _Journal()
    repo = _repository_on(journal)
    processor = AsyncMock()
    processor.process_event.side_effect = journal.note("process_event", FIRED)

    pod_id = uuid4()
    schedules = [
        ScheduleEntity(
            id=uuid4(),
            user_id=uuid4(),
            pod_id=pod_id,
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "users", "operations": ["INSERT"]},
        )
        for _ in range(2)
    ]
    repo.find_by_pod_table_event.return_value = schedules

    handler = DatastoreEventHandler(
        schedule_repository=repo, schedule_processor=processor
    )
    await handler.handle_datastore_event(
        DatastoreRecordEvent.create(
            pod_id=pod_id,
            table_name="users",
            record_id="rec_1",
            operation=DatastoreRecordOperation.INSERT,
            payload={"id": "rec_1"},
            actor_id=schedules[0].user_id,
        )
    )

    assert journal.entries == [
        "commit",
        "process_event",
        "record_fire",
        "commit",
        "process_event",
        "record_fire",
    ]


def _filtered_schedule(pod_id=None) -> ScheduleEntity:
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=pod_id or uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "tickets", "operations": ["INSERT"]},
        filter_instruction="Only tickets a customer marked urgent.",
    )


def _insert_event(schedule: ScheduleEntity, *, owner_user_id=None):
    return DatastoreRecordEvent.create(
        pod_id=schedule.pod_id,
        table_name="tickets",
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1", "priority": "low"},
        actor_id=schedule.user_id,
        owner_user_id=owner_user_id,
    )


@pytest.mark.asyncio
async def test_a_filter_skip_is_recorded_as_a_filtered_run_with_its_decision():
    """PS-SCHED-012: skipped is recorded as skipped, and says which decision."""
    repo = AsyncMock()
    processor = AsyncMock()
    outcomes = FakeFilterOutcomes()
    schedule = _filtered_schedule()
    repo.find_by_pod_table_event.return_value = [schedule]
    decision_id = uuid4()
    verdict = ScheduleFilterVerdict(
        proceed=False,
        decision_id=decision_id,
        output={"should_proceed": False, "decision_id": str(decision_id)},
    )
    processor.process_event.return_value = ScheduleEventOutcome(
        fired=False, verdict=verdict
    )
    row_owner = uuid4()
    event = _insert_event(schedule, owner_user_id=row_owner)

    handler = DatastoreEventHandler(repo, processor, run_outcomes=outcomes)
    result = await handler.handle_datastore_event(event)

    assert result == []
    [skip] = outcomes.recorded
    assert skip.kind == "filtered"
    assert skip.schedule_id == schedule.id
    assert skip.source_event_id == str(event.event_id)
    # The row owner's, like every other run this row would have produced.
    assert skip.user_id == row_owner
    assert skip.llm_output == verdict.output
    assert skip.metadata is not None
    assert skip.metadata["record_id"] == "rec_1"


@pytest.mark.asyncio
async def test_an_undecided_filter_fails_its_fire_without_holding_back_the_rest():
    """Not raised: the decision is recorded under the event, so a redelivery
    would read back the same open question and drop the other schedules again."""
    repo = AsyncMock()
    processor = AsyncMock()
    outcomes = FakeFilterOutcomes()
    undecided = _filtered_schedule()
    firing = _filtered_schedule(pod_id=undecided.pod_id)
    repo.find_by_pod_table_event.return_value = [undecided, firing]
    decision_id = uuid4()
    processor.process_event.side_effect = [
        ScheduleFilterUndecidedError(decision_id),
        FIRED,
    ]

    handler = DatastoreEventHandler(repo, processor, run_outcomes=outcomes)
    result = await handler.handle_datastore_event(_insert_event(undecided))

    assert result == [firing.id]
    [failure] = outcomes.recorded
    assert failure.kind == "undecided"
    assert failure.schedule_id == undecided.id
    assert failure.decision_id == decision_id
    assert failure.user_id == undecided.user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("has_owner", [True, False])
async def test_only_a_row_with_an_owner_is_judged_as_personal(has_owner: bool):
    """A row carries an owner only on an RLS table, where it is theirs alone."""
    repo = AsyncMock()
    processor = AsyncMock()
    processor.process_event.return_value = FIRED
    schedule = _filtered_schedule()
    repo.find_by_pod_table_event.return_value = [schedule]

    handler = DatastoreEventHandler(repo, processor, run_outcomes=FakeFilterOutcomes())
    await handler.handle_datastore_event(
        _insert_event(schedule, owner_user_id=uuid4() if has_owner else None)
    )

    assert processor.process_event.await_args.kwargs["personal"] is has_owner

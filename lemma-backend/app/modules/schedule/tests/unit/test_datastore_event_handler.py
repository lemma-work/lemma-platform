from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.infrastructure.db.session_uow import SESSION_UOW_KEY
from app.core.origin import OriginKind
from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.services.datastore_event_handler import DatastoreEventHandler
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.schedule.tests.fakes import (
    RecordingFilterTaskQueue,
    RecordingScheduleEventPublisher,
    ScriptedScheduleFilter,
)


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
    processor.process_event.return_value = True

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
    processor.process_event.return_value = True
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
    processor.process_event.return_value = True
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
    processor.process_event.return_value = True
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
    processor.process_event.return_value = True
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

    def note(self, name: str):
        async def _record(*_args, **_kwargs):
            self.entries.append(name)

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

    Each schedule is handed on through I/O this session does not own -- the
    outbox write on a session of its own, or a Redis enqueue -- and a pooled
    connection must not sit idle across it. One release before the loop would
    not do: the FILTERED/TRIGGERED fire row written for schedule N re-dirties
    the session before schedule N+1 is handed on.

    The property is an ordering, so the assertion is on the sequence. A commit
    *count* would pass just as well with the commit in the wrong place.
    """
    journal = _Journal()
    repo = _repository_on(journal)
    processor = AsyncMock()
    processor.process_event.side_effect = journal.note("process_event")

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


def _filtered_schedule(pod_id, *, table: str = "users"):
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=pod_id,
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": table, "operations": ["INSERT"]},
        filter_instruction="Only rows that need a human.",
    )


def _insert(pod_id, *, owner_user_id=None, table: str = "users"):
    return DatastoreRecordEvent.create(
        pod_id=pod_id,
        table_name=table,
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1"},
        actor_id=uuid4(),
        owner_user_id=owner_user_id,
    )


@pytest.mark.asyncio
async def test_a_filtered_schedule_is_queued_instead_of_evaluated_inline():
    """The shared consumer must not wait on a model.

    Every pod's record events go through one consumer, one at a time, so a
    filter evaluated here holds back every other pod's triggers for as long as
    the model takes. The decision is the filter task's; this only queues it.
    """
    journal = _Journal()
    repo = _repository_on(journal)
    model = ScriptedScheduleFilter(proceed=True)
    publisher = RecordingScheduleEventPublisher()
    queue = RecordingFilterTaskQueue(journal.entries)
    pod_id = uuid4()
    schedule = _filtered_schedule(pod_id)
    repo.find_by_pod_table_event.return_value = [schedule]
    row_owner = uuid4()
    event = _insert(pod_id, owner_user_id=row_owner)

    handler = DatastoreEventHandler(
        repo, ScheduleProcessor(model, publisher), filter_task_queue=queue
    )
    fired = await handler.handle_datastore_event(event)

    assert model.evaluated == [], "the model was called on the shared consumer"
    assert publisher.fired == [], "a filtered schedule fired before its filter ran"
    assert fired == []
    [queued] = queue.queued
    assert queued.schedule_id == schedule.id
    # The job's identity is the event's, which is what lets a redelivery
    # collapse into the run the first delivery already started.
    assert queued.source_event_id == str(event.event_id)
    assert queued.user_id == row_owner
    assert queued.metadata["record_id"] == "rec_1"
    # Released before the enqueue, and no fire stamped: the task decides.
    assert journal.entries == ["commit", "enqueue"]


@pytest.mark.asyncio
async def test_an_unfiltered_schedule_still_fires_directly():
    repo = AsyncMock()
    model = ScriptedScheduleFilter(proceed=True)
    publisher = RecordingScheduleEventPublisher()
    queue = RecordingFilterTaskQueue()
    pod_id = uuid4()
    filtered = _filtered_schedule(pod_id)
    direct = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=pod_id,
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "users", "operations": ["INSERT"]},
    )
    repo.find_by_pod_table_event.return_value = [filtered, direct]
    event = _insert(pod_id)

    handler = DatastoreEventHandler(
        repo, ScheduleProcessor(model, publisher), filter_task_queue=queue
    )
    fired = await handler.handle_datastore_event(event)

    assert fired == [direct.id]
    assert [fire.schedule_id for fire in publisher.fired] == [direct.id]
    assert publisher.fired[0].source_event_id == str(event.event_id)
    assert [job.schedule_id for job in queue.queued] == [filtered.id]
    assert model.evaluated == []


@pytest.mark.asyncio
async def test_a_shared_row_queues_its_filter_to_run_as_the_schedule_owner():
    """No row owner travels, and the task reads that as the schedule owner."""
    repo = AsyncMock()
    queue = RecordingFilterTaskQueue()
    pod_id = uuid4()
    repo.find_by_pod_table_event.return_value = [_filtered_schedule(pod_id)]

    handler = DatastoreEventHandler(repo, AsyncMock(), filter_task_queue=queue)
    await handler.handle_datastore_event(_insert(pod_id, owner_user_id=None))

    assert [job.user_id for job in queue.queued] == [None]


@pytest.mark.asyncio
async def test_the_filter_task_is_queued_as_a_data_trigger():
    """Origin travels with a queued job, so the filter's work is DATA_TRIGGER."""
    repo = AsyncMock()
    queue = RecordingFilterTaskQueue()
    pod_id = uuid4()
    repo.find_by_pod_table_event.return_value = [_filtered_schedule(pod_id)]

    handler = DatastoreEventHandler(repo, AsyncMock(), filter_task_queue=queue)
    await handler.handle_datastore_event(_insert(pod_id))

    assert [job.origin for job in queue.queued] == [OriginKind.DATA_TRIGGER]


@pytest.mark.asyncio
async def test_a_direct_fire_reports_its_latency_at_info(caplog):
    """Fire latency is the number a datastore trigger is judged by in production."""
    repo = AsyncMock()
    publisher = RecordingScheduleEventPublisher()
    pod_id = uuid4()
    repo.find_by_pod_table_event.return_value = [
        ScheduleEntity(
            id=uuid4(),
            user_id=uuid4(),
            pod_id=pod_id,
            schedule_type=ScheduleType.DATASTORE,
            config={"table_name": "users", "operations": ["INSERT"]},
        )
    ]
    handler = DatastoreEventHandler(
        repo,
        ScheduleProcessor(ScriptedScheduleFilter(), publisher),
        filter_task_queue=RecordingFilterTaskQueue(),
    )

    with caplog.at_level("INFO"):
        await handler.handle_datastore_event(_insert(pod_id))

    assert "schedule.fire.latency_ms" in caplog.text

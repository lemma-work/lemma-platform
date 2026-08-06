from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

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
    assert (
        repo.record_fire.await_args.kwargs["status"] == ScheduleFireStatus.FILTERED
    )


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

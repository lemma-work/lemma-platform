from uuid import UUID, uuid4

import pytest

from app.modules.schedule.domain.errors import ScheduleFilterUndecidedError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.schedule.tests.fakes import FakeScheduleFilter, RecordingPublisher
from app.modules.usage.domain.errors import UsageLimitExceededError


def _schedule(**updates) -> ScheduleEntity:
    values = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "custom"},
        "filter_instruction": "Accept relevant events",
    }
    values.update(updates)
    return ScheduleEntity(**values)


def _processor(
    schedule_filter: FakeScheduleFilter | None = None,
) -> tuple[ScheduleProcessor, RecordingPublisher]:
    publisher = RecordingPublisher()
    return ScheduleProcessor(
        schedule_filter or FakeScheduleFilter(), publisher
    ), publisher


@pytest.mark.asyncio
async def test_processor_rejects_missing_or_inactive_schedule():
    processor, publisher = _processor()

    with pytest.raises(ValueError, match="schedule is required"):
        await processor.process_event(schedule=None, payload={}, user_id=uuid4())
    outcome = await processor.process_event(
        schedule=_schedule(is_active=False), payload={}, user_id=uuid4()
    )
    assert outcome.fired is False
    assert outcome.verdict is None
    assert publisher.published == []


@pytest.mark.asyncio
async def test_processor_needs_the_event_id_before_it_filters():
    """The filter's decision is filed under the event; without an id there is
    nothing to file it under, so nothing is asked."""
    schedule_filter = FakeScheduleFilter()
    processor, _ = _processor(schedule_filter)

    with pytest.raises(ValueError, match="source_event_id"):
        await processor.process_event(
            schedule=_schedule(), payload={"id": 1}, user_id=uuid4()
        )
    assert schedule_filter.calls == []


@pytest.mark.asyncio
async def test_processor_returns_a_skip_with_its_verdict_and_publishes_nothing():
    schedule_filter = FakeScheduleFilter(proceed=False)
    processor, publisher = _processor(schedule_filter)

    outcome = await processor.process_event(
        schedule=_schedule(),
        payload={"id": 1},
        user_id=uuid4(),
        source_event_id="provider:event-1",
    )

    assert outcome.fired is False
    assert outcome.filtered is not None
    assert outcome.filtered.output["should_proceed"] is False
    assert publisher.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        UsageLimitExceededError(),
        RuntimeError("provider unavailable"),
        ScheduleFilterUndecidedError(UUID(int=7)),
    ],
)
async def test_processor_rethrows_filter_failures_for_the_task_boundary(failure):
    processor, publisher = _processor(FakeScheduleFilter(error=failure))

    with pytest.raises(type(failure)):
        await processor.process_event(
            schedule=_schedule(),
            payload={"id": 1},
            user_id=uuid4(),
            source_event_id="provider:event-1",
        )
    assert publisher.published == []


@pytest.mark.asyncio
async def test_processor_publishes_filter_output_and_source_identity():
    decision_id = str(uuid4())
    output = {"should_proceed": True, "decision_id": decision_id, "category": "urgent"}
    schedule_filter = FakeScheduleFilter(proceed=True, output=output)
    processor, publisher = _processor(schedule_filter)
    schedule = _schedule()

    # The caller's owner is carried through verbatim: the processor never
    # substitutes the schedule owner, so an RLS row owner survives filtering.
    row_owner = uuid4()
    assert row_owner != schedule.user_id

    outcome = await processor.process_event(
        schedule=schedule,
        payload={"id": 1},
        user_id=row_owner,
        metadata={"provider": "custom"},
        source_event_id="provider:event-1",
        personal=True,
    )

    assert outcome.fired is True
    assert outcome.filtered is None
    [fire] = publisher.published
    assert fire.payload == {"id": 1}
    assert fire.user_id == row_owner
    assert fire.metadata == {"provider": "custom"}
    assert fire.llm_output == output
    assert fire.source_event_id == "provider:event-1"
    # The filter judged the event on its owner's behalf, knowing it is theirs.
    [call] = schedule_filter.calls
    assert call.source_event_id == "provider:event-1"
    assert call.owner_id == row_owner
    assert call.personal is True


@pytest.mark.asyncio
async def test_processor_without_a_filter_fires_with_no_llm_output():
    schedule_filter = FakeScheduleFilter()
    processor, publisher = _processor(schedule_filter)

    outcome = await processor.process_event(
        schedule=_schedule(filter_instruction=None),
        payload={"id": 1},
        user_id=uuid4(),
        source_event_id="provider:event-1",
    )

    assert outcome.fired is True
    assert outcome.verdict is None
    assert schedule_filter.calls == []
    assert publisher.published[0].llm_output is None

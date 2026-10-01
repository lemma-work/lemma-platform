"""What a triaged event comes to: fired, skipped, or held -- and asked about.

The processor is real and its triage a fake answering one route, so these pin
what each route does downstream: only act publishes, ignore is a skip carrying
its decision, digest and ask are held, and only a held ask puts a question to
its person -- once the hold is recorded, and not again for an event already
answered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.events import (
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.schedule.domain.errors import ScheduleTriageUndecidedError
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.domain.triage import TriageConfig, TriageRoute
from app.modules.schedule.services.datastore_event_handler import DatastoreEventHandler
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.schedule.tests.fakes import (
    FakeFilterOutcomes,
    FakeQuestions,
    FakeTriage,
    RecordingPublisher,
)

pytestmark = pytest.mark.unit

TRIAGE = TriageConfig.model_validate(
    {
        "decider": "ticket-triage",
        "question": "action",
        "routes": {"urgent": "act", "fyi": "digest", "unsure": "ask", "spam": "ignore"},
        "digest": {"cron": "0 9 * * *"},
    }
)


def _schedule(schedule_type: ScheduleType = ScheduleType.DATASTORE) -> ScheduleEntity:
    config = (
        {"table_name": "tickets", "operations": ["INSERT"]}
        if schedule_type is ScheduleType.DATASTORE
        else {"source": "custom"}
    )
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        name="ticket-inbox",
        schedule_type=schedule_type,
        config=config,
        triage=TRIAGE,
    )


async def _process(route: TriageRoute):
    publisher = RecordingPublisher()
    processor = ScheduleProcessor(
        event_publisher=publisher, triage_service=FakeTriage(route)
    )
    outcome = await processor.process_event(
        schedule=_schedule(ScheduleType.WEBHOOK),
        payload={"id": 7, "subject": "Down again"},
        user_id=uuid4(),
        metadata={"source": "custom"},
        source_event_id="custom:7",
    )
    return outcome, publisher


async def test_act_fires_with_the_decision_as_its_llm_output():
    outcome, publisher = await _process(TriageRoute.ACT)

    assert outcome.fired is True
    assert outcome.filtered is None and outcome.held is None
    [fire] = publisher.published
    assert fire.source_event_id == "custom:7"
    assert fire.payload == {"id": 7, "subject": "Down again"}
    assert fire.llm_output is not None
    assert fire.llm_output["route"] == "act"
    assert fire.llm_output["decision_id"] == str(outcome.triage.decision_id)


async def test_ignore_is_a_skip_carrying_its_decision():
    outcome, publisher = await _process(TriageRoute.IGNORE)

    assert outcome.fired is False
    assert publisher.published == []
    assert outcome.held is None
    assert outcome.filtered is not None
    assert outcome.filtered.output["route"] == "ignore"
    assert outcome.filtered.decision_id == outcome.triage.decision_id


@pytest.mark.parametrize("route", [TriageRoute.DIGEST, TriageRoute.ASK])
async def test_digest_and_ask_hold_the_event_and_wake_nobody(route):
    outcome, publisher = await _process(route)

    assert outcome.fired is False
    assert publisher.published == []
    assert outcome.filtered is None
    assert outcome.held is not None
    assert outcome.held.route is route


async def test_a_triage_without_its_adapter_refuses_rather_than_firing():
    processor = ScheduleProcessor(event_publisher=RecordingPublisher())

    with pytest.raises(RuntimeError, match="triage adapter"):
        await processor.process_event(
            schedule=_schedule(),
            payload={},
            user_id=uuid4(),
            source_event_id="row:1",
        )


def _row_event(schedule: ScheduleEntity, *, owner_user_id=None):
    return DatastoreRecordEvent.create(
        pod_id=schedule.pod_id,
        table_name="tickets",
        record_id="rec_1",
        operation=DatastoreRecordOperation.INSERT,
        payload={"id": "rec_1", "priority": "low"},
        actor_id=schedule.user_id,
        owner_user_id=owner_user_id,
    )


def _handler(
    schedule: ScheduleEntity,
    triage: FakeTriage,
    outcomes: FakeFilterOutcomes,
    questions: FakeQuestions,
):
    repo = AsyncMock()
    repo.find_by_pod_table_event.return_value = [schedule]
    processor = ScheduleProcessor(
        event_publisher=RecordingPublisher(), triage_service=triage
    )
    handler = DatastoreEventHandler(
        repo, processor, run_outcomes=outcomes, questions=questions
    )
    return handler, repo


async def test_an_ask_is_held_and_its_person_is_asked_once_it_is_recorded():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    questions = FakeQuestions()
    handler, _repo = _handler(
        schedule, FakeTriage(TriageRoute.ASK, answer="unsure"), outcomes, questions
    )
    row_owner = uuid4()
    event = _row_event(schedule, owner_user_id=row_owner)

    fired = await handler.handle_datastore_event(event)

    assert fired == []
    [held] = outcomes.recorded
    assert held.kind == "held"
    assert held.user_id == row_owner
    assert held.payload == {"id": "rec_1", "priority": "low"}
    assert held.llm_output is not None and held.llm_output["route"] == "ask"
    [question] = questions.asked
    # The row's owner is asked: the event, and the decision, are theirs alone.
    assert question.recipient_user_id == row_owner
    assert question.run_id == outcomes.held[str(event.event_id)].id
    assert question.decider == "ticket-triage"
    assert question.question == "action"
    assert question.routes == TRIAGE.routes
    assert question.schedule_name == "ticket-inbox"


async def test_a_digest_is_held_without_asking_anyone():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    questions = FakeQuestions()
    handler, _repo = _handler(
        schedule, FakeTriage(TriageRoute.DIGEST), outcomes, questions
    )

    await handler.handle_datastore_event(_row_event(schedule))

    [held] = outcomes.recorded
    assert held.kind == "held"
    assert held.user_id == schedule.user_id
    assert questions.asked == []


async def test_a_redelivered_event_already_answered_is_not_asked_about_again():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    questions = FakeQuestions()
    handler, _repo = _handler(
        schedule, FakeTriage(TriageRoute.ASK), outcomes, questions
    )
    event = _row_event(schedule)
    outcomes.answered.add(str(event.event_id))

    await handler.handle_datastore_event(event)

    assert questions.asked == []


async def test_an_ignored_row_is_a_filtered_run_with_its_decision():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    handler, repo = _handler(
        schedule, FakeTriage(TriageRoute.IGNORE), outcomes, FakeQuestions()
    )

    await handler.handle_datastore_event(_row_event(schedule))

    [skip] = outcomes.recorded
    assert skip.kind == "filtered"
    assert skip.llm_output is not None and skip.llm_output["route"] == "ignore"
    # The skip's run stamps the fire status itself.
    repo.record_fire.assert_not_awaited()


async def test_an_acted_row_fires_and_stamps_triggered():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    handler, repo = _handler(
        schedule, FakeTriage(TriageRoute.ACT), outcomes, FakeQuestions()
    )

    fired = await handler.handle_datastore_event(_row_event(schedule))

    assert fired == [schedule.id]
    assert outcomes.recorded == []
    assert repo.record_fire.await_args.kwargs["status"] is ScheduleFireStatus.TRIGGERED


async def test_an_undecided_triage_is_a_failed_fire_with_its_own_error_type():
    schedule = _schedule()
    outcomes = FakeFilterOutcomes()
    decision_id = uuid4()
    handler, _repo = _handler(
        schedule,
        FakeTriage(TriageRoute.ACT, error=ScheduleTriageUndecidedError(decision_id)),
        outcomes,
        FakeQuestions(),
    )

    assert await handler.handle_datastore_event(_row_event(schedule)) == []

    [failure] = outcomes.recorded
    assert failure.kind == "undecided"
    assert failure.decision_id == decision_id
    assert failure.error_type == "ScheduleTriageUndecided"

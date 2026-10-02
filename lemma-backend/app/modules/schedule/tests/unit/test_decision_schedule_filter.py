"""A schedule's filter is a decision, and extraction a second stage behind it.

The decisions contract is faked behind the adapter's own port, so what is under
test is the adapter: what it asks, under which subject and on whose behalf,
what it makes of the answer, and when it pays for extraction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.decisions.contracts.decide import Lane, Rung
from app.modules.schedule.domain.errors import (
    ScheduleFilterInterruptedError,
    ScheduleFilterUndecidedError,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    _MAX_EVENT_CHARS,
    PROCEED,
    DecisionScheduleFilter,
)
from app.modules.schedule.tests.fakes import (
    FakeDecisions,
    FakeExtractor,
    FakeOrganizations,
)

pytestmark = pytest.mark.unit

INSTRUCTION = "Only tickets a customer marked urgent."
EXTRA_FIELDS = {
    "type": "object",
    "properties": {
        "should_proceed": {"type": "boolean"},
        "reason": {"type": "string"},
        "category": {"type": "string"},
    },
    "required": ["should_proceed", "category"],
}


def _schedule(**updates: object) -> ScheduleEntity:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "custom"},
        "filter_instruction": INSTRUCTION,
        "visibility": "POD",
    }
    values.update(updates)
    return ScheduleEntity.model_validate(values)


def _filter(
    decisions: FakeDecisions,
    extractor: FakeExtractor | None = None,
    organizations: FakeOrganizations | None = None,
) -> DecisionScheduleFilter:
    return DecisionScheduleFilter(
        decide_filter=decisions,
        extractor=extractor or FakeExtractor(),
        organization_of=organizations or FakeOrganizations(),
    )


async def _ask(
    subject: DecisionScheduleFilter,
    schedule: ScheduleEntity,
    *,
    output_schema: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
    source_event_id: str = "provider:event-1",
    owner_id: UUID | None = None,
    personal: bool = False,
):
    return await subject.filter_event(
        schedule=schedule,
        instruction=schedule.filter_instruction or "",
        output_schema=output_schema,
        event_payload=payload or {"ticket": {"id": 7, "priority": "low"}},
        source_event_id=source_event_id,
        owner_id=owner_id or schedule.user_id,
        personal=personal,
    )


async def test_an_event_the_decision_turns_down_never_pays_for_extraction():
    decisions = FakeDecisions(proceed=False)
    extractor = FakeExtractor({"category": "billing"})

    verdict = await _ask(
        _filter(decisions, extractor), _schedule(), output_schema=EXTRA_FIELDS
    )

    assert verdict.proceed is False
    assert extractor.calls == []
    assert verdict.output == {
        "should_proceed": False,
        "decision_id": str(decisions.decision_ids[0]),
    }
    assert verdict.decision_id == decisions.decision_ids[0]


async def test_a_passing_event_is_extracted_and_carries_its_decision():
    decisions = FakeDecisions(proceed=True, by=Rung.SYSTEM_ONE)
    # A schema field that collides with ours must not overwrite what the event
    # actually fired on.
    extractor = FakeExtractor(
        {"category": "billing", "should_proceed": False, "decision_id": "made-up"}
    )

    verdict = await _ask(
        _filter(decisions, extractor), _schedule(), output_schema=EXTRA_FIELDS
    )

    assert verdict.proceed is True
    assert verdict.output == {
        "category": "billing",
        "should_proceed": True,
        "decision_id": str(decisions.decision_ids[0]),
    }
    [extraction] = extractor.calls
    # The decision answered `should_proceed`; the model is not asked it again.
    properties = extraction.schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"reason", "category"}
    assert extraction.schema["required"] == ["category"]
    assert extraction.event == {"ticket": {"id": 7, "priority": "low"}}


@pytest.mark.parametrize(
    "declared",
    [
        None,
        {"type": "object"},
        {"type": "object", "properties": {"should_proceed": {"type": "boolean"}}},
        {
            "properties": {
                "should_proceed": {"type": "boolean"},
                "reason": {"type": "string"},
            }
        },
        {"properties": "not an object"},
    ],
)
async def test_a_schema_asking_for_nothing_beyond_the_verdict_is_not_extracted(
    declared: dict[str, object] | None,
):
    decisions = FakeDecisions(proceed=True)
    extractor = FakeExtractor({"category": "billing"})

    verdict = await _ask(
        _filter(decisions, extractor), _schedule(), output_schema=declared
    )

    assert extractor.calls == []
    assert verdict.output == {
        "should_proceed": True,
        "decision_id": str(decisions.decision_ids[0]),
    }


async def test_an_open_decision_is_a_failed_evaluation_not_a_skip():
    decisions = FakeDecisions(proceed=None)
    extractor = FakeExtractor()

    with pytest.raises(ScheduleFilterUndecidedError) as raised:
        await _ask(_filter(decisions, extractor), _schedule())

    assert raised.value.decision_id == decisions.decision_ids[0]
    assert extractor.calls == []


async def test_a_decision_a_provider_failure_left_open_is_retried_not_failed():
    """The decisions module asks an interrupted decision again, so the retry can land."""
    decisions = FakeDecisions(proceed=None, interrupted=True)

    with pytest.raises(ScheduleFilterInterruptedError) as raised:
        await _ask(_filter(decisions, FakeExtractor()), _schedule())

    assert raised.value.decision_id == decisions.decision_ids[0]


async def test_the_decision_is_filed_under_the_schedule_and_its_event():
    """The subject is what makes a redelivery read the recorded answer."""
    decisions = FakeDecisions(proceed=True)
    schedule = _schedule()
    subject = _filter(decisions)

    await _ask(subject, schedule, source_event_id="github:delivery-9")
    await _ask(subject, schedule, source_event_id="github:delivery-9")

    assert [ask.subject for ask in decisions.asked] == [
        f"schedule:{schedule.id}:github:delivery-9"
    ] * 2


async def test_the_instruction_is_asked_as_one_ambient_yes_no_question():
    decisions = FakeDecisions(proceed=True)

    await _ask(_filter(decisions), _schedule())

    definition = decisions.asked[0].definition
    assert list(definition.questions) == [PROCEED]
    question = definition.questions[PROCEED]
    assert question.type == "yes_no"
    assert question.prompt == INSTRUCTION
    assert definition.guidance is None
    assert definition.rules == []
    assert definition.policy.lane is Lane.AMBIENT
    assert definition.policy.escalate_to_model is True
    assert definition.input.max_chars == _MAX_EVENT_CHARS


async def test_an_instruction_too_long_for_a_question_is_asked_as_guidance():
    """A filter instruction was never bounded; it must not stop being asked."""
    decisions = FakeDecisions(proceed=False)
    instruction = "Only fire for " + "urgent " * 400

    await _ask(_filter(decisions), _schedule(filter_instruction=instruction))

    definition = decisions.asked[0].definition
    assert definition.guidance == instruction
    assert definition.questions[PROCEED].prompt != instruction


async def test_the_owner_asks_in_the_pods_organization_under_the_schedules_visibility():
    decisions = FakeDecisions(proceed=True)
    organizations = FakeOrganizations()
    schedule = _schedule(visibility="PERSONAL")

    await _ask(_filter(decisions, organizations=organizations), schedule)

    asker = decisions.asked[0].asker
    assert asker.user_id == schedule.user_id
    assert asker.pod_id == schedule.pod_id
    assert asker.organization_id == organizations.organization_id
    assert asker.visibility == "PERSONAL"
    assert organizations.asked == [schedule.pod_id]


async def test_a_row_only_its_owner_can_read_is_judged_on_their_record_alone():
    """An RLS row becomes the decision's evidence, so a POD-visible decision
    would show it to every member who can read decisions."""
    decisions = FakeDecisions(proceed=False)
    schedule = _schedule(schedule_type=ScheduleType.DATASTORE, visibility="POD")
    row_owner = uuid4()

    await _ask(_filter(decisions), schedule, owner_id=row_owner, personal=True)

    asker = decisions.asked[0].asker
    assert asker.user_id == row_owner
    assert asker.visibility == "PERSONAL"


async def test_a_schedule_outside_a_pod_asks_without_an_organization():
    decisions = FakeDecisions(proceed=True)
    organizations = FakeOrganizations()

    await _ask(_filter(decisions, organizations=organizations), _schedule(pod_id=None))

    assert decisions.asked[0].asker.organization_id is None
    assert organizations.asked == []


async def test_the_event_reaches_the_decision_as_plain_json():
    """A datastore row can carry a timestamp; the decision takes JSON only."""
    decisions = FakeDecisions(proceed=True)
    at = datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    record_id = uuid4()

    await _ask(
        _filter(decisions),
        _schedule(),
        payload={"at": at, "record": record_id, "n": 3},
    )

    assert decisions.asked[0].state == {
        "at": str(at),
        "record": str(record_id),
        "n": 3,
    }

"""A schedule's triage asks its decider about each event and routes the answer.

The decisions contract is faked behind the adapter's own port, so what is under
test is the adapter: what it asks, under which subject and on whose behalf,
how it routes the answer, what it does with a question left open, and when the
act ceiling sends an event somewhere else.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.decisions.contracts.decide import Lane
from app.modules.schedule.domain.errors import (
    ScheduleFilterInterruptedError,
    ScheduleFilterUndecidedError,
    ScheduleTriageInterruptedError,
    ScheduleTriageUndecidedError,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.domain.triage import TriageConfig, TriageRoute
from app.modules.schedule.infrastructure.adapters.decision_triage import (
    DecisionScheduleTriage,
)
from app.modules.schedule.tests.fakes import (
    FakeActAdmission,
    FakeOrganizations,
    FakeTriageDecisions,
)

pytestmark = pytest.mark.unit

ROUTES = {"urgent": "act", "fyi": "digest", "unsure": "ask", "noise": "ignore"}


def _schedule(**triage_updates: object) -> ScheduleEntity:
    triage: dict[str, object] = {
        "decider": "inbox-triage",
        "question": "action",
        "routes": ROUTES,
        "digest": {"cron": "0 9 * * *"},
    }
    triage.update(triage_updates)
    return ScheduleEntity.model_validate(
        {
            "id": uuid4(),
            "user_id": uuid4(),
            "pod_id": uuid4(),
            "schedule_type": ScheduleType.WEBHOOK,
            "config": {"source": "custom"},
            "visibility": "POD",
            "triage": TriageConfig.model_validate(triage),
        }
    )


def _triage(
    decisions: FakeTriageDecisions, admission: FakeActAdmission | None = None
) -> DecisionScheduleTriage:
    return DecisionScheduleTriage(
        decide_named=decisions,
        organization_of=FakeOrganizations(),
        admit_act=admission or FakeActAdmission(),
    )


async def _sort(
    subject: DecisionScheduleTriage,
    schedule: ScheduleEntity,
    *,
    owner_id: UUID | None = None,
    personal: bool = False,
    source_event_id: str = "provider:event-1",
):
    return await subject.triage_event(
        schedule=schedule,
        event_payload={"subject": "Invoice overdue", "from": "billing@acme.example"},
        source_event_id=source_event_id,
        owner_id=owner_id or schedule.user_id,
        personal=personal,
    )


@pytest.mark.parametrize(
    ("answer", "route"),
    [
        ("urgent", TriageRoute.ACT),
        ("fyi", TriageRoute.DIGEST),
        ("unsure", TriageRoute.ASK),
        ("noise", TriageRoute.IGNORE),
    ],
)
async def test_each_answer_is_routed_by_the_schedules_routes(answer, route):
    decisions = FakeTriageDecisions(answer)

    verdict = await _sort(_triage(decisions), _schedule())

    assert verdict.route is route
    assert verdict.answer == answer
    assert verdict.decision_id == decisions.decision_ids[0]
    assert verdict.output == {
        "decision_id": str(decisions.decision_ids[0]),
        "question": "action",
        "answer": answer,
        "route": route.value,
    }


async def test_the_decider_is_asked_once_per_event_as_the_filter_asks():
    decisions = FakeTriageDecisions("urgent")
    schedule = _schedule()
    owner = uuid4()

    await _sort(
        _triage(decisions),
        schedule,
        owner_id=owner,
        personal=True,
        source_event_id="row:42",
    )

    [asked] = decisions.asked
    assert asked.decider == "inbox-triage"
    # The subject a filter's decision is filed under: a redelivery reads it.
    assert asked.subject == f"schedule:{schedule.id}:row:42"
    assert asked.lane is Lane.AMBIENT
    assert asked.asker.user_id == owner
    assert asked.asker.pod_id == schedule.pod_id
    # A row on an RLS table is its owner's alone, and so is judging it.
    assert asked.asker.visibility == "PERSONAL"
    assert asked.state == {"subject": "Invoice overdue", "from": "billing@acme.example"}


async def test_an_open_question_with_a_routed_fallback_takes_the_fallback():
    decisions = FakeTriageDecisions(None, open_question=True, fallback="unsure")

    verdict = await _sort(_triage(decisions), _schedule())

    assert verdict.route is TriageRoute.ASK
    assert verdict.output["fallback"] is True


async def test_an_open_question_without_a_fallback_is_a_failed_evaluation():
    decisions = FakeTriageDecisions(None, open_question=True)

    with pytest.raises(ScheduleTriageUndecidedError) as raised:
        await _sort(_triage(decisions), _schedule())

    # Recorded and counted exactly as an undecided filter is.
    assert isinstance(raised.value, ScheduleFilterUndecidedError)
    assert raised.value.decision_id == decisions.decision_ids[0]
    assert raised.value.error_type == "ScheduleTriageUndecided"


async def test_a_question_a_failing_rung_left_open_is_asked_again():
    """Even with a routed fallback: the moment failed, not the question."""
    decisions = FakeTriageDecisions(
        None, open_question=True, fallback="unsure", interrupted=True
    )

    with pytest.raises(ScheduleTriageInterruptedError) as raised:
        await _sort(_triage(decisions), _schedule())

    assert isinstance(raised.value, ScheduleFilterInterruptedError)


async def test_an_answer_the_routes_do_not_name_is_a_failed_evaluation():
    """The decider gained an option after the schedule was saved."""
    decisions = FakeTriageDecisions("escalate")

    with pytest.raises(ScheduleTriageUndecidedError):
        await _sort(_triage(decisions), _schedule())


async def test_below_the_ceiling_act_is_admitted_and_acts():
    admission = FakeActAdmission(admitted=2)
    schedule = _schedule(act_per_hour=3)

    verdict = await _sort(
        _triage(FakeTriageDecisions("urgent"), admission),
        schedule,
        source_event_id="provider:event-9",
    )

    assert verdict.route is TriageRoute.ACT
    assert "routed_from" not in verdict.output
    # The place is taken for this event, before its fire is published.
    assert admission.asked == [(schedule.id, "provider:event-9", 3)]


async def test_a_burst_takes_only_the_places_the_hour_has():
    """Every event asks for a place; the one past the ceiling is turned away."""
    admission = FakeActAdmission()
    schedule = _schedule(act_per_hour=1)
    triage = _triage(FakeTriageDecisions("urgent"), admission)

    first = await _sort(triage, schedule, source_event_id="provider:event-1")
    second = await _sort(triage, schedule, source_event_id="provider:event-2")
    replayed = await _sort(triage, schedule, source_event_id="provider:event-1")

    assert first.route is TriageRoute.ACT
    assert second.route is TriageRoute.DIGEST
    # A redelivery of an admitted event is admitted again, not counted twice.
    assert replayed.route is TriageRoute.ACT
    assert admission.admitted == 1


async def test_at_the_ceiling_act_goes_to_the_digest():
    verdict = await _sort(
        _triage(FakeTriageDecisions("urgent"), FakeActAdmission(admitted=3)),
        _schedule(act_per_hour=3),
    )

    assert verdict.route is TriageRoute.DIGEST
    assert verdict.answer == "urgent"
    assert verdict.output["routed_from"] == "act"
    assert verdict.output["route"] == "digest"


async def test_at_the_ceiling_without_a_digest_act_goes_to_a_person():
    schedule = _schedule(
        act_per_hour=1, digest=None, routes={**ROUTES, "fyi": "ignore"}
    )

    verdict = await _sort(
        _triage(FakeTriageDecisions("urgent"), FakeActAdmission(admitted=1)), schedule
    )

    assert verdict.route is TriageRoute.ASK
    assert verdict.output["routed_from"] == "act"


async def test_no_ceiling_and_no_act_never_takes_a_place():
    admission = FakeActAdmission(admitted=1_000)

    assert (
        await _sort(_triage(FakeTriageDecisions("urgent"), admission), _schedule())
    ).route is TriageRoute.ACT
    assert (
        await _sort(
            _triage(FakeTriageDecisions("fyi"), admission), _schedule(act_per_hour=1)
        )
    ).route is TriageRoute.DIGEST
    assert admission.asked == []

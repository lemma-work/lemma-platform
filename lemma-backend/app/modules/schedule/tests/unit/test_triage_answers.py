"""A held event's question, answered: where the event goes, and what it teaches.

The decisions contract is faked at the answer's own seam and the ledger by an
in-memory stand-in for the two reads and the one compare-and-set the answer
makes, so what is under test is the routing: by the options the person was
shown, once, and with nothing run for a question nobody answered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue

from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.contracts import (
    CHOICE_ACTION,
    NotificationClosedEvent,
    NotificationOriginKind,
)
from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunEntity,
    ScheduleRunStatus,
    ScheduleType,
)
from app.modules.schedule.domain.triage import (
    OfferedOption,
    TriageAsk,
    TriageConfig,
    TriageRoute,
)
from app.modules.schedule.handlers.triage_answer_consumer import closed_question
from app.modules.schedule.services.triage_answers import ClosedQuestion, TriageAnswers
from app.modules.schedule.tests.fakes import FakeTeach

pytestmark = pytest.mark.unit

POD = uuid4()
OPTIONS = [
    OfferedOption(key="urgent", label="A customer is waiting.", route=TriageRoute.ACT),
    OfferedOption(key="fyi", label="Worth knowing.", route=TriageRoute.DIGEST),
    OfferedOption(key="spam", label="Noise.", route=TriageRoute.IGNORE),
]


class _Uow:
    def __init__(self) -> None:
        self.events: list[object] = []

    def collect_events(self, events: list[object]) -> None:
        self.events.extend(events)


class _UowFactory:
    def __init__(self) -> None:
        self.opened: list[_Uow] = []

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[_Uow]:
        uow = _Uow()
        self.opened.append(uow)
        yield uow

    @property
    def events(self) -> list[object]:
        return [event for uow in self.opened for event in uow.events]


class _HeldRuns:
    """The ledger's held runs: a read, and the compare-and-set on `held_for`."""

    def __init__(self, run: ScheduleRunEntity) -> None:
        self.runs = {run.id: run}

    async def get(self, run_id: UUID) -> ScheduleRunEntity | None:
        return self.runs.get(run_id)

    async def settle_ask(
        self,
        run_id: UUID,
        *,
        route: TriageRoute,
        llm_output: Mapping[str, JsonValue],
        now: datetime,
    ) -> ScheduleRunEntity | None:
        run = self.runs.get(run_id)
        if run is None or run.held_for is not TriageRoute.ASK:
            return None
        settled = {
            TriageRoute.ACT: {"status": ScheduleRunStatus.RECEIVED, "held_for": None},
            TriageRoute.DIGEST: {"held_for": TriageRoute.DIGEST},
            TriageRoute.IGNORE: {
                "status": ScheduleRunStatus.FILTERED,
                "held_for": None,
                "completed_at": now,
            },
        }[route]
        run = run.model_copy(update={**settled, "llm_output": dict(llm_output)})
        self.runs[run_id] = run
        return run


class _Schedules:
    def __init__(self, schedule: ScheduleEntity) -> None:
        self.schedule = schedule

    async def get(self, schedule_id: UUID) -> ScheduleEntity | None:
        return self.schedule if schedule_id == self.schedule.id else None


def _schedule(*, digest: bool = True) -> ScheduleEntity:
    triage = TriageConfig.model_validate(
        {
            "decider": "ticket-triage",
            "question": "action",
            "routes": {
                "urgent": "act",
                "fyi": "digest" if digest else "ignore",
                "unsure": "ask",
                "spam": "ignore",
            },
            "digest": {"cron": "0 9 * * *"} if digest else None,
        }
    )
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=POD,
        schedule_type=ScheduleType.WEBHOOK,
        config={"source": "custom"},
        triage=triage,
        next_digest_at=datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
        if digest
        else None,
    )


def _held(schedule: ScheduleEntity) -> ScheduleRunEntity:
    return ScheduleRunEntity(
        schedule_id=schedule.id,
        user_id=schedule.user_id,
        source_event_id="custom:7",
        status=ScheduleRunStatus.HELD,
        target_kind="AGENT",
        target_run_id=None,
        payload={"id": 7, "subject": "Down again"},
        metadata={"source": "custom"},
        llm_output={"decision_id": "d", "answer": "unsure", "route": "ask"},
        held_for=TriageRoute.ASK,
    )


def _closed(run: ScheduleRunEntity, answer: str | None, **updates: object):
    values: dict[str, object] = {
        "pod_id": POD,
        "ask": TriageAsk(
            schedule_id=run.schedule_id,
            run_id=run.id,
            decision_id=uuid4(),
            question="action",
            options=OPTIONS,
        ),
        "status": "RESPONDED" if answer else "EXPIRED",
        "answer": answer,
        "responder_user_id": run.user_id if answer else None,
    }
    values.update(updates)
    return ClosedQuestion(**values)  # type: ignore[arg-type]  # assembled per test


def _answers(schedule: ScheduleEntity, run: ScheduleRunEntity, teach: FakeTeach):
    factory = _UowFactory()
    held = _HeldRuns(run)
    answers = TriageAnswers(
        factory,  # type: ignore[arg-type]  # stands in for the unit of work
        teach=teach,
        held_runs=lambda _uow: held,
        schedules=lambda _uow: _Schedules(schedule),
    )
    return answers, held, factory


async def test_act_fires_the_held_event_now_and_teaches_the_decider():
    schedule = _schedule()
    run = _held(schedule)
    teach = FakeTeach()
    answers, held, factory = _answers(schedule, run, teach)
    closed = _closed(run, "urgent")

    assert await answers.settle(closed) is TriageRoute.ACT

    [taught] = teach.taught
    assert taught.answer == "urgent"
    assert taught.question == "action"
    assert taught.decision_id == closed.ask.decision_id
    assert taught.user_id == run.user_id
    assert held.runs[run.id].status is ScheduleRunStatus.RECEIVED
    [fired] = factory.events
    assert isinstance(fired, ScheduleFired)
    # The held event itself, under its own key, as a redrive re-fires a run.
    assert fired.source_event_id == "custom:7"
    assert fired.payload == {"id": 7, "subject": "Down again"}
    assert fired.user_id == run.user_id
    assert fired.llm_output is not None
    assert fired.llm_output["route"] == "act"
    assert fired.llm_output["answered_by"] == "person"


async def test_digest_keeps_it_held_for_the_next_digest():
    schedule = _schedule()
    run = _held(schedule)
    answers, held, factory = _answers(schedule, run, FakeTeach())

    assert await answers.settle(_closed(run, "fyi")) is TriageRoute.DIGEST

    assert held.runs[run.id].held_for is TriageRoute.DIGEST
    assert held.runs[run.id].status is ScheduleRunStatus.HELD
    assert factory.events == []


async def test_ignore_ends_it_as_skipped():
    schedule = _schedule()
    run = _held(schedule)
    answers, held, factory = _answers(schedule, run, FakeTeach())

    assert await answers.settle(_closed(run, "spam")) is TriageRoute.IGNORE

    assert held.runs[run.id].status is ScheduleRunStatus.FILTERED
    assert factory.events == []


async def test_a_second_answer_does_nothing():
    schedule = _schedule()
    run = _held(schedule)
    teach = FakeTeach()
    answers, _held_runs, factory = _answers(schedule, run, teach)

    assert await answers.settle(_closed(run, "urgent")) is TriageRoute.ACT
    assert await answers.settle(_closed(run, "spam")) is None

    assert len(teach.taught) == 1
    assert len(factory.events) == 1


async def test_a_question_nobody_answered_is_skipped_and_teaches_nothing():
    schedule = _schedule()
    run = _held(schedule)
    teach = FakeTeach()
    answers, held, factory = _answers(schedule, run, teach)

    assert await answers.settle(_closed(run, None)) is TriageRoute.IGNORE

    assert teach.taught == []
    assert held.runs[run.id].status is ScheduleRunStatus.FILTERED
    assert held.runs[run.id].llm_output["unanswered"] == "EXPIRED"
    assert factory.events == []


async def test_digest_with_no_digest_left_to_send_it_fires_now():
    """The digest was removed after the question went out."""
    schedule = _schedule(digest=False)
    run = _held(schedule)
    answers, held, factory = _answers(schedule, run, FakeTeach())

    assert await answers.settle(_closed(run, "fyi")) is TriageRoute.ACT

    assert held.runs[run.id].llm_output["routed_from"] == "digest"
    assert len(factory.events) == 1


async def test_a_refused_answer_still_routes_the_event():
    """The person's choice decides the event even if the decision is gone."""
    schedule = _schedule()
    run = _held(schedule)
    answers, held, _factory = _answers(
        schedule, run, FakeTeach(error=DomainError("gone", code="DECISION_NOT_FOUND"))
    )

    assert await answers.settle(_closed(run, "spam")) is TriageRoute.IGNORE
    assert held.runs[run.id].status is ScheduleRunStatus.FILTERED


def _closed_event(**updates: object) -> NotificationClosedEvent:
    ask = TriageAsk(
        schedule_id=uuid4(),
        run_id=uuid4(),
        decision_id=uuid4(),
        question="action",
        options=OPTIONS,
    )
    values: dict[str, object] = {
        "pod_id": POD,
        "notification_id": uuid4(),
        "origin_kind": NotificationOriginKind.SCHEDULE,
        "origin_id": ask.schedule_id,
        "status": "RESPONDED",
        "responder_user_id": uuid4(),
        "answer": "urgent",
        "action": {"type": CHOICE_ACTION, **ask.model_dump(mode="json")},
    }
    values.update(updates)
    return NotificationClosedEvent.model_validate(values)


def test_a_closed_schedule_question_is_read_back_as_it_was_asked():
    event = _closed_event()

    closed = closed_question(event)

    assert closed is not None
    assert closed.answer == "urgent"
    assert closed.status == "RESPONDED"
    assert closed.ask.route_for("urgent") is TriageRoute.ACT
    assert closed.ask.route_for("unsure") is None


def test_anything_else_that_closes_is_not_a_schedules_to_settle():
    assert closed_question(_closed_event(origin_kind="AGENT_RUN")) is None
    assert closed_question(_closed_event(action={"type": CHOICE_ACTION})) is None

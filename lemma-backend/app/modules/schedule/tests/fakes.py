"""Fakes for the schedule filter's ports, each typed against what it stands for.

A fake here fails when its port changes, which a patched attribute never does.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from pydantic import JsonValue

from app.modules.decisions.contracts.decide import (
    Asker,
    DeciderDefinition,
    DecisionEntity,
    Lane,
    Rung,
)
from app.modules.schedule.domain.interfaces import (
    ScheduleEventFilter,
    ScheduleEventPublisher,
    ScheduleFilterOutcomeRecorder,
    ScheduleFilterVerdict,
)
from app.modules.schedule.domain.ports import (
    ScheduleEventTriage,
    TriageQuestion,
    TriageQuestions,
)
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleRunEntity,
    ScheduleRunStatus,
)
from app.modules.schedule.domain.triage import (
    TriageRoute,
    TriageVerdict,
    verdict_output,
)
from app.modules.schedule.infrastructure.adapters.decision_triage import (
    ActCounter,
    DecideNamed,
)
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    DecideFilter,
    FilterFieldExtractor,
    OrganizationLookup,
)
from app.modules.schedule.services.triage_answers import TeachDecision


@dataclass(frozen=True)
class FilterAsk:
    """One question put to the decisions contract."""

    state: JsonValue
    asker: Asker
    definition: DeciderDefinition
    subject: str


class FakeDecisions:
    """`decide`, answering every event the same way: yes, no, or left open."""

    def __init__(
        self, proceed: bool | None, *, by: Rung = Rung.MODEL, interrupted: bool = False
    ) -> None:
        self.proceed = proceed
        self.by = by
        #: Left open by a failing rung rather than an unsure one.
        self.interrupted = interrupted
        self.asked: list[FilterAsk] = []
        self.decision_ids: list[UUID] = []

    async def __call__(
        self,
        *,
        state: JsonValue,
        asker: Asker,
        definition: DeciderDefinition,
        subject: str,
    ) -> DecisionEntity:
        self.asked.append(FilterAsk(state, asker, definition, subject))
        answered = self.proceed is not None
        decision = DecisionEntity.model_validate(
            {
                "pod_id": asker.pod_id,
                "organization_id": asker.organization_id,
                "user_id": asker.user_id,
                "visibility": asker.visibility,
                "decider_scope": "inline",
                "decider_key": "inline:fake",
                "subject_key": subject,
                "answers": (
                    {"proceed": {"value": self.proceed, "by": self.by.value}}
                    if answered
                    else {}
                ),
                "open": [] if answered else ["proceed"],
                "status": "answered" if answered else "abstained",
                "trace": (
                    [{"rung": "model", "outcome": "failed", "questions": ["proceed"]}]
                    if self.interrupted and not answered
                    else [{"rung": "model", "outcome": "abstained", "questions": []}]
                ),
            }
        )
        self.decision_ids.append(decision.id)
        return decision


@dataclass(frozen=True)
class Extraction:
    """One second-stage call: the schema it was asked to fill, and for whom."""

    schema: dict[str, JsonValue]
    event: dict[str, JsonValue]
    user_id: UUID


class FakeExtractor:
    """The second stage, answering with whatever it was built with."""

    def __init__(self, fields: dict[str, JsonValue] | None = None) -> None:
        self.fields = fields or {}
        self.calls: list[Extraction] = []

    async def extract(
        self,
        *,
        schedule: ScheduleEntity,
        instruction: str,
        schema: dict[str, JsonValue],
        event: dict[str, JsonValue],
        user_id: UUID,
        organization_id: UUID | None,
    ) -> dict[str, JsonValue]:
        del schedule, instruction, organization_id
        self.calls.append(Extraction(schema, event, user_id))
        return dict(self.fields)


class FakeOrganizations:
    """The pod -> organization lookup, for one organization."""

    def __init__(self, organization_id: UUID | None = None) -> None:
        self.organization_id = organization_id or uuid4()
        self.asked: list[UUID] = []

    async def __call__(self, pod_id: UUID) -> UUID | None:
        self.asked.append(pod_id)
        return self.organization_id


@dataclass(frozen=True)
class FilterCall:
    schedule: ScheduleEntity
    event_payload: Mapping[str, object]
    source_event_id: str
    owner_id: UUID
    personal: bool


class FakeScheduleFilter:
    """`ScheduleEventFilter` returning one verdict, or raising one error."""

    def __init__(
        self,
        *,
        proceed: bool = True,
        output: dict[str, JsonValue] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.proceed = proceed
        self.output = output
        self.error = error
        self.calls: list[FilterCall] = []

    async def filter_event(
        self,
        *,
        schedule: ScheduleEntity,
        instruction: str,
        output_schema: Mapping[str, object] | None,
        event_payload: Mapping[str, object],
        source_event_id: str,
        owner_id: UUID,
        personal: bool = False,
    ) -> ScheduleFilterVerdict:
        del instruction, output_schema
        self.calls.append(
            FilterCall(schedule, event_payload, source_event_id, owner_id, personal)
        )
        if self.error is not None:
            raise self.error
        decision_id = uuid4()
        return ScheduleFilterVerdict(
            proceed=self.proceed,
            decision_id=decision_id,
            output=self.output
            or {"should_proceed": self.proceed, "decision_id": str(decision_id)},
        )


@dataclass(frozen=True)
class PublishedFire:
    schedule_id: UUID
    payload: Dict[str, Any]
    source_event_id: str
    user_id: UUID
    metadata: Optional[Dict[str, Any]]
    llm_output: Optional[Dict[str, Any]]


class RecordingPublisher(ScheduleEventPublisher):
    """Keeps every `schedule.fired` it is asked to publish."""

    def __init__(self) -> None:
        self.published: list[PublishedFire] = []

    async def publish_schedule_fired(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        source_event_id: str,
        user_id: UUID,
        metadata: Optional[Dict[str, Any]] = None,
        llm_output: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.published.append(
            PublishedFire(
                schedule.id, payload, source_event_id, user_id, metadata, llm_output
            )
        )


@dataclass
class RecordedOutcome:
    kind: str
    schedule_id: UUID
    source_event_id: str
    user_id: UUID | None
    metadata: Mapping[str, object] | None
    llm_output: Mapping[str, object] | None = None
    decision_id: UUID | None = None
    error_type: str | None = None
    payload: Mapping[str, object] | None = None


@dataclass
class FakeFilterOutcomes:
    """The run ledger's side of a filter or triage: skipped, held, undecided.

    A held event is recorded as a `HELD` run the first time and found again on
    a repeat, as the ledger's unique key does; `answered` marks runs a person
    has already settled, so a repeat finds them no longer waiting.
    """

    recorded: list[RecordedOutcome] = field(default_factory=list)
    held: dict[str, ScheduleRunEntity] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)

    async def record_filtered(
        self,
        schedule: ScheduleEntity,
        *,
        source_event_id: str,
        user_id: UUID,
        metadata: Mapping[str, object] | None,
        llm_output: Mapping[str, object],
    ) -> bool:
        self.recorded.append(
            RecordedOutcome(
                "filtered",
                schedule.id,
                source_event_id,
                user_id,
                metadata,
                llm_output=llm_output,
            )
        )
        return True

    async def record_filter_undecided(
        self,
        schedule: ScheduleEntity,
        *,
        source_event_id: str,
        decision_id: UUID | None,
        user_id: UUID | None = None,
        metadata: Mapping[str, object] | None = None,
        error_type: str = "ScheduleFilterUndecided",
    ) -> bool:
        self.recorded.append(
            RecordedOutcome(
                "undecided",
                schedule.id,
                source_event_id,
                user_id,
                metadata,
                decision_id=decision_id,
                error_type=error_type,
            )
        )
        return True

    async def record_held(
        self,
        schedule: ScheduleEntity,
        verdict: TriageVerdict,
        *,
        source_event_id: str,
        user_id: UUID,
        payload: Mapping[str, object],
        metadata: Mapping[str, object] | None,
    ) -> ScheduleRunEntity | None:
        self.recorded.append(
            RecordedOutcome(
                "held",
                schedule.id,
                source_event_id,
                user_id,
                metadata,
                llm_output=verdict.output,
                decision_id=verdict.decision_id,
                payload=payload,
            )
        )
        run = self.held.setdefault(
            source_event_id,
            ScheduleRunEntity(
                schedule_id=schedule.id,
                user_id=user_id,
                source_event_id=source_event_id,
                status=ScheduleRunStatus.HELD,
                target_kind=schedule.target_kind,
                target_run_id=None,
                payload=dict(payload),
                llm_output=dict(verdict.output),
                held_for=verdict.route,
            ),
        )
        if source_event_id in self.answered:
            return run.model_copy(
                update={"status": ScheduleRunStatus.FILTERED, "held_for": None}
            )
        return run


@dataclass(frozen=True)
class TriageAskCall:
    """One event put to a schedule's decider by name."""

    state: JsonValue
    asker: Asker
    decider: str
    subject: str
    lane: Lane


class FakeTriageDecisions:
    """`decide` for a triage: answers one choice question as it was built to.

    `answer` is the option chosen; `open_question` leaves it open, with the
    question's `fallback` as its answer when one is given, as the ladder does;
    `interrupted` says a failing rung left it open.
    """

    def __init__(
        self,
        answer: str | None,
        *,
        question: str = "action",
        open_question: bool = False,
        fallback: str | None = None,
        interrupted: bool = False,
        by: Rung = Rung.RULES,
    ) -> None:
        self.answer = answer
        self.question = question
        self.open_question = open_question
        self.fallback = fallback
        self.interrupted = interrupted
        self.by = by
        self.asked: list[TriageAskCall] = []
        self.decision_ids: list[UUID] = []

    async def __call__(
        self,
        *,
        state: JsonValue,
        asker: Asker,
        decider: str,
        subject: str,
        lane: Lane,
    ) -> DecisionEntity:
        self.asked.append(TriageAskCall(state, asker, decider, subject, lane))
        value = self.fallback if self.open_question else self.answer
        answers: dict[str, JsonValue] = {}
        if value is not None:
            answers[self.question] = {
                "value": value,
                "by": self.by.value,
                "abstained": self.open_question,
            }
        failed = self.interrupted and self.open_question
        decision = DecisionEntity.model_validate(
            {
                "pod_id": asker.pod_id,
                "user_id": asker.user_id,
                "visibility": asker.visibility,
                "decider_scope": "pod",
                "decider_key": decider,
                "decider_name": decider,
                "subject_key": subject,
                "shape": {self.question: {"type": "choice", "options": ["a", "b"]}},
                "answers": answers,
                "open": [self.question] if self.open_question else [],
                "status": "abstained" if self.open_question else "answered",
                "trace": [
                    {
                        "rung": "model",
                        "outcome": "failed" if failed else "answered",
                        "questions": [self.question],
                    }
                ],
                "evidence": json.dumps(state),
            }
        )
        self.decision_ids.append(decision.id)
        return decision


class FakeActCounter:
    """How many act runs the ledger says a schedule started in the last hour."""

    def __init__(self, acted: int = 0) -> None:
        self.acted = acted
        self.asked: list[tuple[UUID, datetime]] = []

    async def __call__(self, schedule_id: UUID, *, since: datetime) -> int:
        self.asked.append((schedule_id, since))
        return self.acted


class FakeTriage:
    """`ScheduleEventTriage` answering every event with one route."""

    def __init__(
        self,
        route: TriageRoute,
        *,
        answer: str = "action-option",
        error: Exception | None = None,
    ) -> None:
        self.route = route
        self.answer = answer
        self.error = error
        self.calls: list[FilterCall] = []

    async def triage_event(
        self,
        *,
        schedule: ScheduleEntity,
        event_payload: Mapping[str, object],
        source_event_id: str,
        owner_id: UUID,
        personal: bool = False,
    ) -> TriageVerdict:
        self.calls.append(
            FilterCall(schedule, event_payload, source_event_id, owner_id, personal)
        )
        if self.error is not None:
            raise self.error
        decision_id = uuid4()
        return TriageVerdict(
            route=self.route,
            decision_id=decision_id,
            question="action",
            answer=self.answer,
            evidence=json.dumps(dict(event_payload), default=str),
            output=verdict_output(
                decision_id=decision_id,
                question="action",
                answer=self.answer,
                route=self.route,
            ),
        )


class FakeQuestions:
    """`TriageQuestions` that keeps every question instead of sending it."""

    def __init__(self) -> None:
        self.asked: list[TriageQuestion] = []

    async def ask(self, question: TriageQuestion) -> UUID | None:
        self.asked.append(question)
        return uuid4()


@dataclass(frozen=True)
class Taught:
    decision_id: UUID
    pod_id: UUID
    question: str
    answer: str
    user_id: UUID


class FakeTeach:
    """The decisions contract's `answer`, recording what a person taught."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.taught: list[Taught] = []

    async def __call__(
        self,
        *,
        decision_id: UUID,
        pod_id: UUID,
        question: str,
        answer: str,
        user_id: UUID,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.taught.append(Taught(decision_id, pod_id, question, answer, user_id))


def _conforms() -> None:
    """Checked by the type checker, never run: each fake satisfies its port."""
    _decide: DecideFilter = FakeDecisions(True)
    _extract: FilterFieldExtractor = FakeExtractor()
    _organizations: OrganizationLookup = FakeOrganizations()
    _filter: ScheduleEventFilter = FakeScheduleFilter()
    _outcomes: ScheduleFilterOutcomeRecorder = FakeFilterOutcomes()
    _named: DecideNamed = FakeTriageDecisions("act")
    _acted: ActCounter = FakeActCounter()
    _triage: ScheduleEventTriage = FakeTriage(TriageRoute.ACT)
    _questions: TriageQuestions = FakeQuestions()
    _teach: TeachDecision = FakeTeach()
    del _decide, _extract, _organizations, _filter, _outcomes
    del _named, _acted, _triage, _questions, _teach

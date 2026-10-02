"""Decisions: the record of asking a decider about one state, and its examples."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain.entity import Entity
from app.modules.decisions.domain.deciders import DeciderScope
from app.modules.decisions.domain.questions import (
    AnswerValue,
    ChoiceQuestion,
    MultiChoiceQuestion,
    Question,
    ScaleQuestion,
)


class Rung(StrEnum):
    """Who answered. The first three are the ladder; the last two resolve it."""

    RULES = "rules"
    SYSTEM_ONE = "system_one"
    MODEL = "model"
    PERSON = "person"
    AGENT = "agent"


MACHINE_RUNGS = frozenset({Rung.RULES, Rung.SYSTEM_ONE, Rung.MODEL})


class Answer(BaseModel):
    """One question's answer and where it came from.

    `distribution` and `confidence` are System One's and exist only when it
    answered. A model's answer carries neither, and nothing here invents one.
    `abstained` marks a fallback taken because no rung committed.
    """

    model_config = ConfigDict(frozen=True)

    value: AnswerValue
    by: Rung
    distribution: dict[str, float] | None = None
    confidence: float | None = None
    abstained: bool = False


class DecisionStatus(StrEnum):
    #: Every question was answered by a machine rung.
    ANSWERED = "answered"
    #: At least one question climbed the whole ladder without an answer.
    ABSTAINED = "abstained"
    #: A person or an agent answered what the machines left open.
    RESOLVED = "resolved"
    #: A person or an agent replaced a machine's answer.
    CORRECTED = "corrected"


class RungOutcome(StrEnum):
    ANSWERED = "answered"
    #: Asked, and could not commit. Final: asking again would say the same.
    ABSTAINED = "abstained"
    #: Configured, but could not be asked this time: rate budget spent, down.
    UNAVAILABLE = "unavailable"
    #: Asked, and failed: a provider error, a broken response.
    FAILED = "failed"
    #: The policy or the lane passed it by.
    SKIPPED = "skipped"
    #: This deployment or organization has no such engine.
    NOT_CONFIGURED = "not_configured"


#: Outcomes that say nothing about the question, only about the moment.
TRANSIENT_OUTCOMES = frozenset({RungOutcome.UNAVAILABLE, RungOutcome.FAILED})


class RungTrace(BaseModel):
    """What one rung did for one decision, for the record and for debugging."""

    model_config = ConfigDict(frozen=True)

    rung: Rung
    outcome: RungOutcome
    questions: list[str]
    model: str | None = None
    latency_ms: int = 0
    input_tokens: int | None = None


class QuestionShape(BaseModel):
    """What a question could be answered with, as it was asked.

    Kept on the decision because the options a caller passed with the call, and
    the whole of an inline definition, exist nowhere else. Keys only: enough to
    check a person's later answer, without keeping every description twice.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    options: list[str] | None = None
    levels: int | None = None


def shape_of(question: Question) -> QuestionShape:
    if isinstance(question, ChoiceQuestion | MultiChoiceQuestion):
        return QuestionShape(type=question.type, options=list(question.options or {}))
    if isinstance(question, ScaleQuestion):
        return QuestionShape(type=question.type, levels=len(question.levels))
    return QuestionShape(type=question.type)


def check_against_shape(shape: QuestionShape, value: AnswerValue) -> AnswerValue:
    """`value` if the question as asked could have been answered with it."""
    checker = _SHAPE_CHECKS.get(shape.type)
    if checker is None:
        raise ValueError(f"unknown question type {shape.type!r}")
    return checker(shape, value)


def _check_choice(shape: QuestionShape, value: AnswerValue) -> AnswerValue:
    if isinstance(value, str) and value in (shape.options or ()):
        return value
    raise ValueError("expected one of the question's option keys")


def _check_multi_choice(shape: QuestionShape, value: AnswerValue) -> AnswerValue:
    options = set(shape.options or ())
    if isinstance(value, list) and all(
        isinstance(item, str) and item in options for item in value
    ):
        return sorted(set(value))
    raise ValueError("expected a list of the question's option keys")


def _check_yes_no(shape: QuestionShape, value: AnswerValue) -> AnswerValue:
    del shape
    if isinstance(value, bool):
        return value
    raise ValueError("expected true or false")


def _check_scale(shape: QuestionShape, value: AnswerValue) -> AnswerValue:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value < (shape.levels or 0)
    ):
        return value
    raise ValueError("expected the index of one of the scale's levels")


_SHAPE_CHECKS = {
    "choice": _check_choice,
    "multi_choice": _check_multi_choice,
    "yes_no": _check_yes_no,
    "scale": _check_scale,
}


def subject_owner(*, visibility: str, user_id: UUID | None) -> UUID | None:
    """Whose subjects a decision is filed among: its asker's when it is PERSONAL.

    A PERSONAL decision is asked once per person and a POD one once per pod,
    so two people deciding the same subject privately each keep their own
    record, and asking about a subject never hands back somebody else's.
    """
    return user_id if visibility == "PERSONAL" else None


class DecisionEntity(Entity):
    """One decision.

    Filed under (`decider_key`, `subject_key`, `subject_owner_id`) when a
    subject is given, which is what makes a decision asked once: a redelivered
    event, a retry or a redrive reads this row instead of asking again.
    """

    pod_id: UUID | None = None
    organization_id: UUID | None = None
    user_id: UUID | None = None
    visibility: str = "POD"
    decider_scope: DeciderScope
    decider_key: str
    decider_name: str | None = None
    decider_version: int | None = None
    subject_key: str | None = None
    shape: dict[str, QuestionShape] = Field(default_factory=dict)
    answers: dict[str, Answer] = Field(default_factory=dict)
    open: list[str] = Field(default_factory=list)
    status: DecisionStatus = DecisionStatus.ANSWERED
    trace: list[RungTrace] = Field(default_factory=list)
    evidence: str | None = None
    evidence_expires_at: datetime | None = None
    answered_by_user_id: UUID | None = None
    answered_at: datetime | None = None

    @property
    def subject_owner_id(self) -> UUID | None:
        return subject_owner(visibility=self.visibility, user_id=self.user_id)

    def visible_to(self, *, pod_id: UUID | None, viewer_id: UUID | None) -> bool:
        """Whether `viewer_id` may read this decision, its evidence included."""
        if self.pod_id != pod_id:
            return False
        return self.visibility == "POD" or (
            viewer_id is not None and self.user_id == viewer_id
        )

    @property
    def interrupted(self) -> bool:
        """Open because a rung could not be asked this time, not because it abstained.

        A recorded decision is final, except this one: nobody has answered it,
        and what left it open was the moment -- a spent budget, a provider
        error -- rather than the question. Asking about the same subject again
        asks again.
        """
        return (
            bool(self.open)
            and self.answered_by_user_id is None
            and any(step.outcome in TRANSIENT_OUTCOMES for step in self.trace)
        )


class ExampleSource(StrEnum):
    #: A person answered or corrected a decision.
    PERSON = "person"
    #: Written with the definition.
    AUTHORED = "authored"


class ExampleEntity(Entity):
    """A labelled state: what a person said a question's answer was.

    `visibility` is the corrected decision's. A PERSONAL example holds evidence
    only its `user_id` may read, so it teaches that person's decisions alone;
    sharing it with the pod would take a choice nobody has made.
    """

    pod_id: UUID | None = None
    decider_key: str
    question_key: str
    value: AnswerValue
    evidence: str
    source: ExampleSource = ExampleSource.PERSON
    visibility: str = "PERSONAL"
    user_id: UUID | None = None
    decision_id: UUID | None = None


class ExampleView(BaseModel):
    """What an engine is shown of an example."""

    model_config = ConfigDict(frozen=True)

    evidence: str
    value: AnswerValue

"""The ports the ladder climbs and the records it keeps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from app.modules.decisions.domain.deciders import Lane
from app.modules.decisions.domain.decisions import (
    Answer,
    DecisionEntity,
    ExampleEntity,
    ExampleView,
    Rung,
)
from app.modules.decisions.domain.questions import Question


@dataclass(frozen=True, slots=True)
class Payer:
    """Whose budget an engine call is charged to, and on whose behalf it runs."""

    user_id: UUID | None
    organization_id: UUID | None
    pod_id: UUID | None
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class Ask:
    """What an engine is asked: the open questions about one rendered state."""

    questions: Mapping[str, Question]
    evidence: str
    guidance: str | None
    examples: Mapping[str, Sequence[ExampleView]]
    lane: Lane
    payer: Payer


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    """What an engine said. Questions it could not commit to are `abstained`."""

    answers: Mapping[str, Answer]
    abstained: frozenset[str]
    model: str | None = None
    input_tokens: int | None = None
    notes: Sequence[str] = field(default_factory=tuple)


class EngineUnavailableError(Exception):
    """The engine could not be asked: not configured, rate-limited, down.

    Not a failure of the decision: the ladder goes on to the next rung and
    records that this one was unavailable.
    """


class EngineFailedError(Exception):
    """The engine was asked and failed: a provider error, a broken response.

    Raised by an engine in place of whatever its provider raised, with the
    original as its cause, so the ladder can go on without a broad catch.
    """


class Engine(Protocol):
    """One rung above rules."""

    @property
    def rung(self) -> Rung: ...

    def is_available(self, *, organization_id: UUID | None) -> bool: ...

    async def answer(self, ask: Ask) -> EngineOutcome: ...


class DecisionStore(Protocol):
    """Where decisions are kept. Each call opens and closes its own unit of work."""

    async def find_by_subject(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        subject_key: str,
        owner_id: UUID | None,
    ) -> DecisionEntity | None: ...

    async def insert(self, decision: DecisionEntity) -> DecisionEntity: ...

    async def get(self, decision_id: UUID) -> DecisionEntity | None: ...

    async def save_answers(self, decision: DecisionEntity) -> DecisionEntity: ...

    async def save_reasked(self, decision: DecisionEntity) -> DecisionEntity: ...


class ExampleStore(Protocol):
    async def recent(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        question_keys: Sequence[str],
        per_question: int,
        viewer_id: UUID | None,
    ) -> Mapping[str, Sequence[ExampleView]]: ...

    async def add(self, examples: Sequence[ExampleEntity]) -> None: ...

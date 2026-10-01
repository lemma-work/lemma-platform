"""Asked once, never recomputed -- unless the moment, not the question, left it open."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

import pytest

from app.modules.decisions.domain.deciders import DeciderDefinition
from app.modules.decisions.domain.decisions import (
    Answer,
    DecisionEntity,
    ExampleEntity,
    ExampleView,
    Rung,
)
from app.modules.decisions.domain.ports import (
    Ask,
    EngineFailedError,
    EngineOutcome,
)
from app.modules.decisions.services.decisions_service import (
    Asker,
    DecideRequest,
    DecisionsService,
)
from app.modules.decisions.services.ladder import Ladder

DEFINITION = DeciderDefinition.model_validate(
    {
        "description": "Does this event matter?",
        "questions": {"proceed": {"type": "yes_no", "prompt": "Does it matter?"}},
    }
)
ASKER = Asker(user_id=UUID(int=1), pod_id=UUID(int=2))


class MemoryDecisions:
    """The decision store's port, in memory."""

    def __init__(self) -> None:
        self.rows: dict[UUID, DecisionEntity] = {}
        self.reasked = 0

    async def find_by_subject(
        self, *, pod_id: UUID | None, decider_key: str, subject_key: str
    ) -> DecisionEntity | None:
        for row in self.rows.values():
            if (row.pod_id, row.decider_key, row.subject_key) == (
                pod_id,
                decider_key,
                subject_key,
            ):
                return row
        return None

    async def insert(self, decision: DecisionEntity) -> DecisionEntity:
        self.rows[decision.id] = decision
        return decision

    async def get(self, decision_id: UUID) -> DecisionEntity | None:
        return self.rows.get(decision_id)

    async def save_answers(self, decision: DecisionEntity) -> DecisionEntity:
        self.rows[decision.id] = decision
        return decision

    async def save_reasked(self, decision: DecisionEntity) -> DecisionEntity:
        self.reasked += 1
        self.rows[decision.id] = decision
        return decision


class NoExamples:
    async def recent(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        question_keys: Sequence[str],
        per_question: int,
    ) -> Mapping[str, Sequence[ExampleView]]:
        return {}

    async def add(self, examples: Sequence[ExampleEntity]) -> None:
        return None


class Scripted:
    """An engine that fails, abstains or answers, one call at a time."""

    def __init__(self, *steps: str) -> None:
        self._steps = list(steps)
        self.calls = 0

    @property
    def rung(self) -> Rung:
        return Rung.MODEL

    def is_available(self, *, organization_id: UUID | None) -> bool:
        return True

    async def answer(self, ask: Ask) -> EngineOutcome:
        self.calls += 1
        step = self._steps.pop(0)
        if step == "fail":
            raise EngineFailedError("provider hiccup")
        if step == "unsure":
            return EngineOutcome(answers={}, abstained=frozenset({"proceed"}))
        return EngineOutcome(
            answers={"proceed": Answer(value=True, by=Rung.MODEL)},
            abstained=frozenset(),
        )


def service(engine: Scripted, decisions: MemoryDecisions) -> DecisionsService:
    return DecisionsService(
        deciders=None,  # type: ignore[arg-type]  # inline definitions only
        decisions=decisions,  # type: ignore[arg-type]
        examples=NoExamples(),  # type: ignore[arg-type]
        ladder=Ladder([engine]),
    )


def request() -> DecideRequest:
    return DecideRequest(
        definition=DEFINITION, state={"subject": "Hi"}, subject="event:1"
    )


@pytest.mark.asyncio
async def test_a_decision_interrupted_by_a_failure_is_asked_again_in_place() -> None:
    decisions = MemoryDecisions()
    engine = Scripted("fail", "answer")
    first = await service(engine, decisions).decide(request(), ASKER)
    assert first.open == ["proceed"]
    assert first.interrupted

    second = await service(engine, decisions).decide(request(), ASKER)
    assert second.id == first.id
    assert second.answers["proceed"].value is True
    assert not second.interrupted
    assert decisions.reasked == 1
    assert len(decisions.rows) == 1


@pytest.mark.asyncio
async def test_a_genuine_abstention_is_final() -> None:
    decisions = MemoryDecisions()
    engine = Scripted("unsure", "answer")
    first = await service(engine, decisions).decide(request(), ASKER)
    assert first.open == ["proceed"]
    assert not first.interrupted

    again = await service(engine, decisions).decide(request(), ASKER)
    assert again.id == first.id
    assert again.open == ["proceed"]
    assert engine.calls == 1

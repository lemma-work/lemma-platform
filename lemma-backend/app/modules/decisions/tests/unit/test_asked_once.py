"""Asked once, never recomputed -- unless the moment, not the question, left it open."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

import pytest

from app.modules.decisions.domain.deciders import DeciderDefinition, DeciderEntity
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
    """The decision store's port, in memory, unique as the SQL index is."""

    def __init__(self) -> None:
        self.rows: dict[UUID, DecisionEntity] = {}
        self.reasked = 0

    async def find_by_subject(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        subject_key: str,
        owner_id: UUID | None,
    ) -> DecisionEntity | None:
        for row in self.rows.values():
            if (
                row.pod_id,
                row.decider_key,
                row.subject_key,
                row.subject_owner_id,
            ) == (pod_id, decider_key, subject_key, owner_id):
                return row
        return None

    async def insert(self, decision: DecisionEntity) -> DecisionEntity:
        if decision.subject_key is not None:
            kept = await self.find_by_subject(
                pod_id=decision.pod_id,
                decider_key=decision.decider_key,
                subject_key=decision.subject_key,
                owner_id=decision.subject_owner_id,
            )
            if kept is not None:
                return kept
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
        viewer_id: UUID | None,
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


ALICE = Asker(user_id=UUID(int=11), pod_id=UUID(int=2), visibility="PERSONAL")
BOB = Asker(user_id=UUID(int=12), pod_id=UUID(int=2), visibility="PERSONAL")


def asked_about(state: dict[str, str], subject: str = "record:42") -> DecideRequest:
    return DecideRequest(definition=DEFINITION, state=state, subject=subject)


@pytest.mark.asyncio
async def test_another_persons_private_decision_is_never_handed_back() -> None:
    """Same decider, same subject: each person decides privately for themselves."""
    decisions = MemoryDecisions()
    engine = Scripted("answer", "answer", "answer")
    alices = await service(engine, decisions).decide(
        asked_about({"subject": "ALICE-PRIVATE"}), ALICE
    )

    bobs = await service(engine, decisions).decide(
        asked_about({"subject": "bob's own"}), BOB
    )

    assert bobs.id != alices.id
    assert bobs.user_id == BOB.user_id
    assert "ALICE-PRIVATE" not in (bobs.evidence or "")
    assert engine.calls == 2
    # Asked once each: Bob asking again reads his own record.
    again = await service(engine, decisions).decide(
        asked_about({"subject": "bob's own"}), BOB
    )
    assert again.id == bobs.id
    assert engine.calls == 2


@pytest.mark.asyncio
async def test_rows_never_return_another_persons_private_decision() -> None:
    decisions = MemoryDecisions()
    engine = Scripted("answer", "answer")
    alices = await service(engine, decisions).decide_rows(
        decider=None,
        definition=DEFINITION,
        rows=[{"id": "7", "subject": "ALICE-PRIVATE"}],
        asker=ALICE,
        id_field="id",
        subject_prefix="inbox",
    )

    bobs = await service(engine, decisions).decide_rows(
        decider=None,
        definition=DEFINITION,
        rows=[{"id": "7", "subject": "bob's own"}],
        asker=BOB,
        id_field="id",
        subject_prefix="inbox",
    )

    assert bobs.rows[0].decision_id != alices.rows[0].decision_id
    assert decisions.rows[bobs.rows[0].decision_id].user_id == BOB.user_id
    assert engine.calls == 2


@pytest.mark.asyncio
async def test_a_shared_decision_is_asked_once_for_the_whole_pod() -> None:
    decisions = MemoryDecisions()
    engine = Scripted("answer")
    shared_by_alice = Asker(
        user_id=ALICE.user_id, pod_id=ALICE.pod_id, visibility="POD"
    )
    shared_by_bob = Asker(user_id=BOB.user_id, pod_id=BOB.pod_id, visibility="POD")
    first = await service(engine, decisions).decide(
        asked_about({"subject": "the team's"}, subject="thread:9"), shared_by_alice
    )

    second = await service(engine, decisions).decide(
        asked_about({"subject": "the team's"}, subject="thread:9"), shared_by_bob
    )

    assert second.id == first.id
    assert engine.calls == 1


@pytest.mark.asyncio
async def test_another_persons_interrupted_decision_is_left_alone() -> None:
    """Re-asking an interrupted record is its owner's to do, never a teammate's."""
    decisions = MemoryDecisions()
    engine = Scripted("fail", "answer")
    alices = await service(engine, decisions).decide(
        asked_about({"subject": "ALICE-PRIVATE"}), ALICE
    )
    assert alices.interrupted

    bobs = await service(engine, decisions).decide(
        asked_about({"subject": "bob's own"}), BOB
    )

    assert bobs.id != alices.id
    assert decisions.reasked == 0
    assert decisions.rows[alices.id].interrupted
    assert decisions.rows[alices.id].evidence == alices.evidence


class HandsBackAnothersRecord(MemoryDecisions):
    """A store whose insert loses to a record the asker may not read."""

    def __init__(self, other: DecisionEntity) -> None:
        super().__init__()
        self.other = other

    async def insert(self, decision: DecisionEntity) -> DecisionEntity:
        return self.other


@pytest.mark.asyncio
async def test_a_record_the_asker_cannot_read_is_never_returned() -> None:
    """The boundary behind the namespace: no path returns someone else's evidence."""
    alices = await service(Scripted("answer"), MemoryDecisions()).decide(
        asked_about({"subject": "ALICE-PRIVATE"}), ALICE
    )

    bobs = await service(Scripted("answer"), HandsBackAnothersRecord(alices)).decide(
        asked_about({"subject": "bob's own"}), BOB
    )

    assert bobs.id != alices.id
    assert "ALICE-PRIVATE" not in (bobs.evidence or "")


class RecordingExamples:
    """The example store's port, keeping what it was asked and given."""

    def __init__(self) -> None:
        self.viewers: list[UUID | None] = []
        self.added: list[ExampleEntity] = []

    async def recent(
        self,
        *,
        pod_id: UUID | None,
        decider_key: str,
        question_keys: Sequence[str],
        per_question: int,
        viewer_id: UUID | None,
    ) -> Mapping[str, Sequence[ExampleView]]:
        self.viewers.append(viewer_id)
        return {}

    async def add(self, examples: Sequence[ExampleEntity]) -> None:
        self.added.extend(examples)


class OneDecider:
    """The decider store's port, holding one pod decider."""

    async def get(self, *, pod_id: UUID, name: str) -> DeciderEntity | None:
        return DeciderEntity(pod_id=pod_id, name=name, definition=DEFINITION)


def teaching_service(
    examples: RecordingExamples, decisions: MemoryDecisions
) -> DecisionsService:
    return DecisionsService(
        deciders=OneDecider(),  # type: ignore[arg-type]
        decisions=decisions,  # type: ignore[arg-type]
        examples=examples,  # type: ignore[arg-type]
        ladder=Ladder([Scripted("answer", "answer")]),
    )


@pytest.mark.asyncio
async def test_examples_are_read_as_the_person_asking() -> None:
    examples = RecordingExamples()

    await teaching_service(examples, MemoryDecisions()).decide(
        DecideRequest(decider="email-triage", state={"subject": "hi"}), BOB
    )

    assert examples.viewers == [BOB.user_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["PERSONAL", "POD"])
async def test_a_correction_keeps_the_visibility_of_what_it_corrected(
    visibility: str,
) -> None:
    examples = RecordingExamples()
    decisions = MemoryDecisions()
    teaching = teaching_service(examples, decisions)
    asker = Asker(user_id=ALICE.user_id, pod_id=ALICE.pod_id, visibility=visibility)
    decided = await teaching.decide(
        DecideRequest(decider="email-triage", state={"subject": "ALICE-PRIVATE"}),
        asker,
    )

    await teaching.answer(
        decision_id=decided.id,
        pod_id=ALICE.pod_id,  # type: ignore[arg-type]
        answers={"proceed": False},
        by=Rung.PERSON,
        user_id=ALICE.user_id,  # type: ignore[arg-type]
    )

    [example] = examples.added
    assert example.visibility == visibility
    assert example.user_id == ALICE.user_id

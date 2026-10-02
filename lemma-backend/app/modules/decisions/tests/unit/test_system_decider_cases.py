"""The system deciders' cases are well formed, and the policy guards hold on them.

Whether an engine gets them right is `scripts/evaluate_system_deciders.py`'s
job, against real engines. What CI can hold is the part that never depends on
an engine: every case asks a real decider with answers its questions allow, and
an engine that says the forbidden thing is refused by the policy itself.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.decisions.domain.deciders import Lane
from app.modules.decisions.domain.decisions import Answer, Rung
from app.modules.decisions.domain.ports import Ask, EngineOutcome, Payer
from app.modules.decisions.domain.questions import validate_answer
from app.modules.decisions.services.decisions_service import asked_questions
from app.modules.decisions.services.ladder import Ladder
from app.modules.decisions.services.rendering import render
from app.modules.decisions.services.system_decider_cases import SYSTEM_DECIDER_CASES
from app.modules.decisions.services.system_deciders import system_decider


@pytest.mark.parametrize("case", SYSTEM_DECIDER_CASES, ids=lambda case: case.name)
def test_every_case_asks_a_real_decider_with_allowed_answers(case) -> None:
    definition = system_decider(case.decider)
    assert definition is not None
    questions = asked_questions(definition, case.options)
    for key, answers in {**case.accepted, **case.forbidden}.items():
        for answer in answers:
            validate_answer(questions[key], answer)


class SaysEverything:
    """An engine that answers the forbidden thing with full confidence."""

    def __init__(self, answers: dict[str, Answer]) -> None:
        self._answers = answers

    @property
    def rung(self) -> Rung:
        return Rung.SYSTEM_ONE

    def is_available(self, *, organization_id: UUID | None) -> bool:
        return True

    async def answer(self, ask: Ask) -> EngineOutcome:
        return EngineOutcome(answers=self._answers, abstained=frozenset())


@pytest.mark.asyncio
async def test_no_engine_can_approve_for_the_session() -> None:
    definition = system_decider("system:approval_reply")
    assert definition is not None
    engine = SaysEverything(
        {
            "decision": Answer(
                value="approve_for_session", by=Rung.SYSTEM_ONE, confidence=1.0
            )
        }
    )
    result = await Ladder([engine]).climb(
        definition=definition,
        questions=definition.questions,
        rendered=render({"request": "x", "reply": "always"}, definition.input),
        examples={},
        payer=Payer(user_id=None, organization_id=None, pod_id=None),
        lane=Lane.INTERACTIVE,
    )
    assert result.answers["decision"].value == "not_an_answer"
    assert result.open == ["decision"]

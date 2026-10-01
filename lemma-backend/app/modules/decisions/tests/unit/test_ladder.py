"""The ladder: which rung answers, what a policy lets stand, and what stays open."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import pytest

from app.modules.decisions.domain.deciders import DeciderDefinition, Lane
from app.modules.decisions.domain.decisions import Answer, Rung, RungOutcome
from app.modules.decisions.domain.ports import (
    Ask,
    EngineFailedError,
    EngineOutcome,
    EngineUnavailableError,
    Payer,
)
from app.modules.decisions.services.ladder import Ladder
from app.modules.decisions.services.rendering import render

PAYER = Payer(user_id=None, organization_id=None, pod_id=None)


class FakeEngine:
    """An engine that answers from a table, or is unavailable, or fails."""

    def __init__(
        self,
        rung: Rung,
        answers: Mapping[str, Answer] | None = None,
        *,
        abstained: frozenset[str] = frozenset(),
        available: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self._rung = rung
        self._answers = dict(answers or {})
        self._abstained = abstained
        self._available = available
        self._raises = raises
        self.asked: list[Ask] = []

    @property
    def rung(self) -> Rung:
        return self._rung

    def is_available(self, *, organization_id: UUID | None) -> bool:
        return self._available

    async def answer(self, ask: Ask) -> EngineOutcome:
        self.asked.append(ask)
        if self._raises is not None:
            raise self._raises
        return EngineOutcome(
            answers={
                key: value
                for key, value in self._answers.items()
                if key in ask.questions
            },
            abstained=self._abstained,
            model=f"{self._rung.value}-model",
        )


def triage(**policy: object) -> DeciderDefinition:
    return DeciderDefinition.model_validate(
        {
            "description": "What to do with an email.",
            "input": {"fields": ["from", "subject", "labels"]},
            "questions": {
                "action": {
                    "type": "choice",
                    "prompt": "What should happen?",
                    "options": {
                        "act": "Needs doing now.",
                        "ask": "Needs a person.",
                        "ignore": "Newsletters and promotions.",
                    },
                    "fallback": "ask",
                }
            },
            "rules": [
                {
                    "when": "contains(labels, 'PROMOTIONS')",
                    "answer": {"action": "ignore"},
                }
            ],
            "policy": policy,
        }
    )


async def climb(
    ladder: Ladder,
    definition: DeciderDefinition,
    state: dict[str, object],
    lane: Lane = Lane.AMBIENT,
):
    return await ladder.climb(
        definition=definition,
        questions=definition.questions,
        rendered=render(state, definition.input),
        examples={},
        payer=PAYER,
        lane=lane,
    )


EMAIL = {
    "from": "a@acme.com",
    "subject": "Invoice 42",
    "labels": ["INBOX"],
    "body": "secret",
}


@pytest.mark.asyncio
async def test_rules_answer_first_and_no_engine_is_asked() -> None:
    system_one = FakeEngine(
        Rung.SYSTEM_ONE, {"action": Answer(value="act", by=Rung.SYSTEM_ONE)}
    )
    result = await climb(
        Ladder([system_one]), triage(), {**EMAIL, "labels": ["PROMOTIONS"]}
    )
    assert result.answers["action"].value == "ignore"
    assert result.answers["action"].by is Rung.RULES
    assert result.open == []
    assert system_one.asked == []


@pytest.mark.asyncio
async def test_engines_see_only_the_input_view() -> None:
    system_one = FakeEngine(
        Rung.SYSTEM_ONE,
        {"action": Answer(value="act", by=Rung.SYSTEM_ONE, confidence=0.9)},
    )
    await climb(Ladder([system_one]), triage(), EMAIL)
    assert "secret" not in system_one.asked[0].evidence
    assert "Invoice 42" in system_one.asked[0].evidence


@pytest.mark.asyncio
async def test_a_low_confidence_system_one_answer_goes_on_to_the_model() -> None:
    system_one = FakeEngine(
        Rung.SYSTEM_ONE,
        {"action": Answer(value="act", by=Rung.SYSTEM_ONE, confidence=0.3)},
    )
    model = FakeEngine(Rung.MODEL, {"action": Answer(value="ignore", by=Rung.MODEL)})
    result = await climb(Ladder([system_one, model]), triage(), EMAIL)
    assert result.answers["action"].value == "ignore"
    assert result.answers["action"].by is Rung.MODEL
    assert [step.outcome for step in result.trace][-2:] == [
        RungOutcome.ABSTAINED,
        RungOutcome.ANSWERED,
    ]


@pytest.mark.asyncio
async def test_interactive_decisions_do_not_wait_for_a_second_opinion() -> None:
    system_one = FakeEngine(
        Rung.SYSTEM_ONE,
        {"action": Answer(value="act", by=Rung.SYSTEM_ONE, confidence=0.3)},
    )
    model = FakeEngine(Rung.MODEL, {"action": Answer(value="ignore", by=Rung.MODEL)})
    result = await climb(
        Ladder([system_one, model]), triage(), EMAIL, lane=Lane.INTERACTIVE
    )
    assert model.asked == []
    assert result.open == ["action"]
    assert result.answers["action"] == Answer(
        value="ask", by=Rung.MODEL, abstained=True
    )


@pytest.mark.asyncio
async def test_without_system_one_the_model_is_the_engine() -> None:
    system_one = FakeEngine(Rung.SYSTEM_ONE, available=False)
    model = FakeEngine(Rung.MODEL, {"action": Answer(value="act", by=Rung.MODEL)})
    result = await climb(
        Ladder([system_one, model]), triage(), EMAIL, lane=Lane.INTERACTIVE
    )
    assert result.answers["action"].value == "act"
    assert result.trace[1].outcome is RungOutcome.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_an_unavailable_or_failing_rung_leaves_the_question_open_with_its_fallback() -> (
    None
):
    system_one = FakeEngine(Rung.SYSTEM_ONE, raises=EngineUnavailableError("down"))
    model = FakeEngine(Rung.MODEL, raises=EngineFailedError("provider exploded"))
    result = await climb(Ladder([system_one, model]), triage(), EMAIL)
    assert result.open == ["action"]
    assert result.answers["action"].value == "ask"
    assert result.answers["action"].abstained
    assert [step.outcome for step in result.trace][1:] == [
        RungOutcome.UNAVAILABLE,
        RungOutcome.FAILED,
    ]


@pytest.mark.asyncio
async def test_rules_only_answers_never_stand_from_an_engine() -> None:
    definition = triage(rules_only={"action": ["act"]})
    system_one = FakeEngine(
        Rung.SYSTEM_ONE,
        {"action": Answer(value="act", by=Rung.SYSTEM_ONE, confidence=0.99)},
    )
    result = await climb(Ladder([system_one]), definition, EMAIL)
    assert result.open == ["action"]


@pytest.mark.asyncio
async def test_required_confidence_is_never_met_by_a_model() -> None:
    definition = triage(require_confidence={"action": {"act": 0.9}})
    model = FakeEngine(Rung.MODEL, {"action": Answer(value="act", by=Rung.MODEL)})
    result = await climb(Ladder([model]), definition, EMAIL)
    assert result.open == ["action"]
    confident = FakeEngine(
        Rung.SYSTEM_ONE,
        {"action": Answer(value="act", by=Rung.SYSTEM_ONE, confidence=0.95)},
    )
    assert (await climb(Ladder([confident]), definition, EMAIL)).answers[
        "action"
    ].value == "act"


@pytest.mark.asyncio
async def test_a_yes_no_inside_the_band_is_passed_on() -> None:
    definition = DeciderDefinition.model_validate(
        {
            "description": "Should this wake the agent?",
            "questions": {"proceed": {"type": "yes_no", "prompt": "Does it matter?"}},
        }
    )
    unsure = FakeEngine(
        Rung.SYSTEM_ONE,
        {
            "proceed": Answer(
                value=True, by=Rung.SYSTEM_ONE, distribution={"yes": 0.55, "no": 0.45}
            )
        },
    )
    model = FakeEngine(Rung.MODEL, {"proceed": Answer(value=False, by=Rung.MODEL)})
    result = await climb(Ladder([unsure, model]), definition, EMAIL)
    assert result.answers["proceed"] == Answer(value=False, by=Rung.MODEL)


@pytest.mark.asyncio
async def test_a_bug_in_an_engine_is_not_hidden_as_an_abstention() -> None:
    broken = FakeEngine(Rung.SYSTEM_ONE, raises=KeyError("a bug, not a provider"))
    with pytest.raises(KeyError):
        await climb(Ladder([broken]), triage(), EMAIL)

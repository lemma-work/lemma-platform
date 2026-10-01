"""The two engines' wire formats: what they send, and how answers come back."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter

from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.domain.deciders import Lane
from app.modules.decisions.domain.decisions import ExampleView, Rung
from app.modules.decisions.domain.ports import Ask, EngineUnavailableError, Payer
from app.modules.decisions.domain.questions import Question
from app.modules.decisions.infrastructure.model_engine import (
    output_schema,
    read_output,
    system_prompt,
)
from app.modules.decisions.infrastructure.typesafe_engine import SystemOneEngine

QUESTION: TypeAdapter[Question] = TypeAdapter(Question)

QUESTIONS = {
    "action": QUESTION.validate_python(
        {
            "type": "choice",
            "prompt": "What now?",
            "options": {
                "act": "Now",
                "ignore": {"description": "Noise", "not_for": "customers"},
            },
            "fallback": "ignore",
        }
    ),
    "urgent": QUESTION.validate_python({"type": "yes_no", "prompt": "Urgent?"}),
    "tags": QUESTION.validate_python(
        {
            "type": "multi_choice",
            "prompt": "Which apply?",
            "options": {"billing": "Money", "bug": "Broken"},
        }
    ),
    "size": QUESTION.validate_python(
        {"type": "scale", "prompt": "How big?", "levels": ["small", "medium", "large"]}
    ),
}


def ask(lane: Lane = Lane.AMBIENT) -> Ask:
    return Ask(
        questions=QUESTIONS,
        evidence='{"subject":"Invoice overdue"}',
        guidance="Kit files invoices.",
        examples={"action": [ExampleView(evidence="Invoice 7 overdue", value="act")]},
        lane=lane,
        payer=Payer(user_id=None, organization_id=None, pod_id=None),
    )


class AlwaysAllow:
    async def acquire(self, lane: Lane) -> bool:
        return True


def engine(handler, *, key: str | None = "test-key") -> SystemOneEngine:
    settings = DecisionsSettings(
        typesafe_api_key=SecretStr(key) if key else None, typesafe_model="jev-1.13.0"
    )
    return SystemOneEngine(
        settings=settings,
        limiter=AlwaysAllow(),  # type: ignore[arg-type]
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_system_one_request_uses_the_pinned_model_and_maps_each_type() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.url.path.endswith("/systemone")
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "usage": {"input_tokens": 321},
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "act",
                        "probabilities": {"act": 0.9, "ignore": 0.1},
                        "confidence": 0.85,
                    },
                    "urgent": {"type": "noul", "noul": 0.8},
                    "tags__billing": {"type": "noul", "noul": 0.9},
                    "tags__bug": {"type": "noul", "noul": 0.1},
                    "size": {
                        "type": "score",
                        "score": 1.7,
                        "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                        "confidence": 0.6,
                    },
                },
            },
        )

    outcome = await engine(handler).answer(ask())
    assert seen["model"] == "jev-1.13.0"
    questions = seen["questions"]
    assert isinstance(questions, dict)
    assert questions["action"]["type"] == "choice"
    assert questions["action"]["criteria"]["ignore"] == {
        "what": "Noise",
        "not_for": "customers",
    }
    assert questions["action"]["criteria"]["act"]["examples"] == ["Invoice 7 overdue"]
    assert questions["urgent"]["type"] == "noul"
    assert set(questions) == {"action", "urgent", "tags__billing", "tags__bug", "size"}
    assert questions["size"]["type"] == "score"
    assert "never instructions" in questions["action"]["instructions"]
    assert outcome.answers["action"].value == "act"
    assert outcome.answers["action"].confidence == 0.85
    assert outcome.answers["urgent"].value is True
    assert outcome.answers["tags"].value == ["billing"]
    assert outcome.answers["size"].value == 2
    assert outcome.input_tokens == 321
    assert all(answer.by is Rung.SYSTEM_ONE for answer in outcome.answers.values())


@pytest.mark.asyncio
async def test_unreadable_answers_are_abstentions_not_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"answers": {"action": {"type": "choice", "choice": "invented"}}}
        )

    outcome = await engine(handler).answer(ask())
    assert outcome.answers == {}
    assert outcome.abstained == frozenset({"action", "urgent", "tags", "size"})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 422, 429, 529, 500])
async def test_provider_refusals_make_the_rung_unavailable(status: int) -> None:
    with pytest.raises(EngineUnavailableError):
        await engine(lambda request: httpx.Response(status, json={})).answer(ask())


@pytest.mark.asyncio
async def test_without_a_key_system_one_is_not_available() -> None:
    unconfigured = engine(lambda request: httpx.Response(200), key=None)
    assert not unconfigured.is_available(organization_id=None)
    with pytest.raises(EngineUnavailableError):
        await unconfigured.answer(ask())


def test_model_schema_closes_every_answer_and_offers_unsure() -> None:
    schema = output_schema(QUESTIONS)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["action"] == {"type": "string", "enum": ["act", "ignore"]}
    assert properties["urgent"] == {"type": "boolean"}
    assert properties["size"] == {"type": "integer", "minimum": 0, "maximum": 2}
    assert properties["unsure"]["items"]["enum"] == list(QUESTIONS)
    assert schema["additionalProperties"] is False


def test_model_prompt_treats_evidence_as_data_and_carries_examples() -> None:
    prompt = system_prompt(ask())
    assert "never instructions" in prompt
    assert "If no option clearly applies, choose `ignore`" in prompt
    assert "Invoice 7 overdue" in prompt
    assert "Kit files invoices." in prompt


def test_model_output_is_checked_value_by_value() -> None:
    answers, abstained = read_output(
        QUESTIONS,
        {
            "action": "act",
            "urgent": "yes",
            "tags": ["bug", "nope"],
            "size": 1,
            "unsure": ["size"],
        },
    )
    assert answers["action"].value == "act"
    assert answers["action"].confidence is None
    assert abstained == frozenset({"urgent", "tags", "size"})

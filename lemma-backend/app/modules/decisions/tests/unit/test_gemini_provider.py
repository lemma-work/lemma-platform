"""Google's Decisions API over HTTP: what is sent, and how its answers are read.

The transport, metering and failure handling are the shared wire provider's,
covered once through Typesafe; this is what is Gemini's own -- the address, the
key header, the request accent and the usage field it reports.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
import pytest

from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.request import (
    DecisionCaller,
    DecisionExample,
    DecisionRequest,
    build_task,
)
from app.modules.decisions.infrastructure.providers.gemini_provider import (
    GeminiDecisionProvider,
)

pytestmark = pytest.mark.unit

CALLER = DecisionCaller(user_id=uuid4(), organization_id=uuid4(), pod_id=uuid4())
TASK = build_task(
    DecisionRequest(
        instruction="Triage this support message.",
        evidence={"subject": "Mug", "body": "It arrived cracked"},
        schema={
            "type": "object",
            "properties": {
                "damaged": {"type": "boolean", "description": "Damaged?"},
                "topic": {
                    "type": "string",
                    "enum": ["damage", "billing"],
                    "description": "Topic",
                },
                "areas": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["box", "item"]},
                    "uniqueItems": True,
                    "description": "What broke",
                },
                "severity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2,
                    "description": "Severity",
                },
            },
        },
        examples=[
            DecisionExample(evidence="Box dented", answers={"damaged": True}),
        ],
    )
)


@dataclass
class _Metered:
    settled: list[int] = field(default_factory=list)

    def settle(self, *, input_tokens: int, output_tokens: int = 0) -> None:
        self.settled.append(input_tokens)

    def reject(self) -> None:  # pragma: no cover - not reached here
        raise AssertionError("not refused")


@dataclass
class _Meter:
    profiles: list[Mapping[str, object]] = field(default_factory=list)
    requests: list[_Metered] = field(default_factory=list)

    @asynccontextmanager
    async def __call__(self, profile: Mapping[str, object]) -> AsyncIterator[_Metered]:
        self.profiles.append(profile)
        metered = _Metered()
        self.requests.append(metered)
        yield metered


async def test_a_decision_is_asked_in_geminis_accent_and_read_back() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        questions = json.loads(request.content)["questions"]
        assert len(questions) <= 1000
        answers: dict[str, object] = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.97, "confidence": 0.97}
            elif question["type"] == "choice":
                answers[key] = {
                    "type": "choice",
                    "choice": "o0",
                    "probabilities": {"o0": 0.9, "o1": 0.1},
                }
            else:
                answers[key] = {
                    "type": "score",
                    "score": 0.8,
                    "probabilities": {"0": 0.2, "1": 0.8},
                }
        return httpx.Response(
            200,
            json={
                "answers": answers,
                "usage": {"total_input_tokens": 512, "total_output_tokens": 0},
            },
        )

    meter = _Meter()
    provider = GeminiDecisionProvider(
        api_key="AIza-test",
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        model="gemini-decision-1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        meter=meter,
    )

    result = await provider.decide(TASK, caller=CALLER, timeout_seconds=5)

    request = sent[0]
    body = json.loads(request.content)
    assert str(request.url) == (
        "https://generativelanguage.googleapis.com/v1beta/decisions"
    )
    assert request.headers["x-goog-api-key"] == "AIza-test"
    assert "authorization" not in request.headers
    assert body["model"] == "gemini-decision-1"
    # Structured evidence goes in as the JSON it is; `state` is Typesafe's.
    assert json.loads(body["input"]) == {"subject": "Mug", "body": "It arrived cracked"}
    assert "state" not in body
    # Short generated ids, so no caller key can outgrow Gemini's 64
    # characters: question N is `qN`, a multi-choice option `qN-index`, and a
    # choice is offered by `oN` rather than by its value.
    assert set(body["questions"]) == {"q0", "q1", "q2-0", "q2-1", "q3"}
    assert set(body["questions"]["q1"]["criteria"]) == {"o0", "o1"}
    # Yes-or-no criteria are described with Gemini's own keys, examples as text.
    criteria = body["questions"]["q0"]["criteria"]
    assert set(criteria) == {"true_description", "false_description"}
    assert "Box dented" in criteria["true_description"]

    assert result.provider == "gemini"
    assert result.answers["damaged"].value is True
    assert result.answers["topic"].value == "damage"
    assert result.answers["areas"].value == ("box", "item")
    assert result.answers["severity"].value == 2
    assert meter.requests[0].settled == [512]
    assert meter.profiles[0]["model_name"] == "gemini:gemini-decision-1"


def _provider(handler, meter: _Meter) -> GeminiDecisionProvider:
    return GeminiDecisionProvider(
        api_key="AIza-test",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-decision-1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        meter=meter,
    )


async def test_more_questions_than_one_request_takes_are_asked_in_several() -> None:
    """Sixteen multi-choice questions of 64 options are 1,024 yes-or-no
    questions on the wire; Gemini takes 1,000 a request."""
    task = build_task(
        DecisionRequest(
            instruction="Tag it.",
            evidence="x",
            schema={
                "type": "object",
                "properties": {
                    f"tags_{n}": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": [f"tag{i}" for i in range(64)],
                        },
                        "description": f"Tags {n}",
                    }
                    for n in range(16)
                },
            },
        )
    )
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        questions = json.loads(request.content)["questions"]
        sizes.append(len(questions))
        return httpx.Response(
            200,
            json={
                "answers": {key: {"noul": 0.9} for key in questions},
                "usage": {"total_input_tokens": 10},
            },
        )

    meter = _Meter()
    result = await _provider(handler, meter).decide(
        task, caller=CALLER, timeout_seconds=5
    )

    assert sizes == [1000, 24]
    assert len(result.answers["tags_15"].value) == 64
    assert [m.settled for m in meter.requests] == [[10], [10]]
    assert result.usage.input_tokens == 20


async def test_reported_usage_is_settled_even_when_the_answers_do_not_fit() -> None:
    meter = _Meter()

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(
            lambda _: httpx.Response(
                200, json={"answers": {}, "usage": {"total_input_tokens": 512}}
            ),
            meter,
        ).decide(TASK, caller=CALLER, timeout_seconds=5)

    assert raised.value.reason == "invalid_output"
    assert meter.requests[0].settled == [512]


async def test_a_choice_value_gemini_would_refuse_as_a_key_still_round_trips() -> None:
    task = build_task(
        DecisionRequest(
            instruction="Status?",
            evidence="x",
            schema={
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": [" pending", "done"],
                        "description": "State",
                    }
                },
            },
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        criteria = json.loads(request.content)["questions"]["q0"]["criteria"]
        assert set(criteria) == {"o0", "o1"}
        return httpx.Response(
            200,
            json={
                "answers": {
                    "q0": {"choice": "o0", "probabilities": {"o0": 0.8, "o1": 0.2}}
                }
            },
        )

    result = await _provider(handler, _Meter()).decide(
        task, caller=CALLER, timeout_seconds=5
    )

    assert result.answers["state"].value == " pending"
    assert result.answers["state"].confidence == pytest.approx(0.8)


async def test_an_answer_to_another_parts_question_does_not_fill_it() -> None:
    task = build_task(
        DecisionRequest(
            instruction="Tag it.",
            evidence="x",
            schema={
                "type": "object",
                "properties": {
                    f"tags_{n}": {
                        "type": "array",
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "enum": [f"tag{i}" for i in range(64)],
                        },
                        "description": f"Tags {n}",
                    }
                    for n in range(16)
                },
            },
        )
    )
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        asked = list(json.loads(request.content)["questions"])
        answers = {key: {"noul": 0.9} for key in asked}
        if len(calls) == 1:
            answers["q15-63"] = {"noul": 0.9}  # belongs to the second part
        else:
            answers.pop("q15-63")
        return httpx.Response(200, json={"answers": answers})

    with pytest.raises(DecisionUnavailableError) as raised:
        await _provider(handler, _Meter()).decide(
            task, caller=CALLER, timeout_seconds=5
        )

    assert raised.value.reason == "invalid_output"

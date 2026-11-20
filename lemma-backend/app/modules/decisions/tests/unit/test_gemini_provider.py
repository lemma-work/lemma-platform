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
        answers: dict[str, object] = {}
        for key, question in questions.items():
            if question["type"] == "noul":
                answers[key] = {"type": "noul", "noul": 0.97, "confidence": 0.97}
            elif question["type"] == "choice":
                answers[key] = {
                    "type": "choice",
                    "choice": "damage",
                    "probabilities": {"damage": 0.9, "billing": 0.1},
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
    # Gemini keys allow only [A-Za-z0-9_-]: a multi-choice option is `-index`.
    assert {"areas-0", "areas-1"} <= body["questions"].keys()
    assert all(
        key.replace("-", "").replace("_", "").isalnum() for key in body["questions"]
    )
    # Yes-or-no criteria are described with Gemini's own keys, examples as text.
    criteria = body["questions"]["damaged"]["criteria"]
    assert set(criteria) == {"true_description", "false_description"}
    assert "Box dented" in criteria["true_description"]

    assert result.provider == "gemini"
    assert result.answers["damaged"].value is True
    assert result.answers["topic"].value == "damage"
    assert result.answers["areas"].value == ("box", "item")
    assert result.answers["severity"].value == 2
    assert meter.requests[0].settled == [512]
    assert meter.profiles[0]["model_name"] == "gemini:gemini-decision-1"

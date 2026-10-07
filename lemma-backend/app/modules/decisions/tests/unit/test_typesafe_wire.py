"""Mapping decisions onto System One's three question types and back."""

from __future__ import annotations

import json

import pytest

from app.modules.decisions.domain.request import (
    DecisionExample,
    DecisionRequest,
    build_task,
)
from app.modules.decisions.infrastructure.providers.typesafe_wire import (
    WireAnswerError,
    build_request,
    parse_response,
)

pytestmark = pytest.mark.unit

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "oneOf": [
                {"const": "billing", "description": "Money"},
                {"const": "bug"},
            ],
            "description": "What is it about?",
        },
        "urgent": {"type": "boolean", "description": "Reply today?"},
        "severity": {
            "type": "integer",
            "oneOf": [
                {"const": 1, "description": "Cosmetic"},
                {"const": 2, "description": "Blocking"},
            ],
            "description": "How bad?",
        },
        "labels": {
            "type": "array",
            "items": {"type": "string", "enum": ["vip", "refund"]},
            "uniqueItems": True,
            "description": "Which apply?",
        },
    },
}


def _task(**overrides: object):
    fields: dict[str, object] = {
        "instruction": "Triage support email.",
        "evidence": {"subject": "Charged twice"},
        "schema": SCHEMA,
    }
    fields.update(overrides)
    return build_task(DecisionRequest(**fields))  # type: ignore[arg-type]


def _answers(**answers: object) -> bytes:
    return json.dumps(
        {"model": "jev-1", "answers": answers, "usage": {"input_tokens": 812}}
    ).encode()


FULL = {
    "category": {"choice": "billing", "probabilities": {"billing": 0.9, "bug": 0.1}},
    "urgent": {"noul": 0.3},
    "severity": {"probabilities": {"0": 0.2, "1": 0.8}},
    "labels__vip": {"noul": 0.1},
    "labels__refund": {"noul": 0.95},
}


def test_each_kind_becomes_its_system_one_type() -> None:
    request = build_request(_task(), model="jev-latest")
    questions = request.body["questions"]
    assert isinstance(questions, dict)

    assert questions["category"] == {
        "type": "choice",
        "instructions": "Triage support email.\n\nQuestion: What is it about?",
        "criteria": {"billing": "Money", "bug": "bug"},
    }
    assert questions["urgent"]["type"] == "noul"  # type: ignore[index]
    assert questions["severity"]["criteria"] == ["Cosmetic", "Blocking"]  # type: ignore[index]
    assert questions["labels__vip"]["type"] == "noul"  # type: ignore[index]
    assert set(request.questions) == {
        "category",
        "urgent",
        "severity",
        "labels__vip",
        "labels__refund",
    }
    assert request.body["state"] == {"subject": "Charged twice"}
    assert request.body["model"] == "jev-latest"


def test_text_evidence_is_wrapped_into_a_state_object() -> None:
    request = build_request(_task(evidence="Charged twice"), model="m")

    assert request.body["state"] == {"evidence": "Charged twice"}


def test_examples_are_filed_under_the_option_they_were_answered_with() -> None:
    task = _task(
        examples=[
            DecisionExample(evidence="Refund me", answers={"category": "billing"}),
            DecisionExample(evidence="Server down", answers={"urgent": True}),
            DecisionExample(evidence="Big client", answers={"labels": ("vip",)}),
        ]
    )

    questions = build_request(task, model="m").body["questions"]
    assert isinstance(questions, dict)

    assert questions["category"]["criteria"]["billing"] == {  # type: ignore[index]
        "what": "Money",
        "examples": ["Refund me"],
    }
    assert questions["urgent"]["criteria"]["true"]["examples"] == [  # type: ignore[index]
        "Server down"
    ]
    assert questions["labels__vip"]["criteria"]["true"]["examples"] == [  # type: ignore[index]
        "Big client"
    ]


def test_confidence_is_the_probability_of_the_value_chosen() -> None:
    task = _task()
    request = build_request(task, model="m")

    answers, model, input_tokens = parse_response(
        _answers(**FULL), task, request.questions
    )

    assert {k: (a.value, a.confidence) for k, a in answers.items()} == {
        "category": ("billing", 0.9),
        "urgent": (False, 0.7),
        "severity": (2, 0.8),
        "labels": (("refund",), 0.9),
    }
    assert (model, input_tokens) == ("jev-1", 812)


def test_a_score_without_a_distribution_rounds_to_a_level() -> None:
    task = _task()
    request = build_request(task, model="m")

    answers, _, _ = parse_response(
        _answers(**{**FULL, "severity": {"score": 0.2}}), task, request.questions
    )

    assert (answers["severity"].value, answers["severity"].confidence) == (1, None)


@pytest.mark.parametrize(
    "broken",
    [
        {"category": {"choice": "refunds"}},
        {"urgent": {"noul": 1.4}},
        {"urgent": {"noul": None}},
        {"severity": {"score": 7}},
        {"labels__vip": None},
    ],
    ids=[
        "choice-not-offered",
        "probability-over-one",
        "no-probability",
        "off-scale",
        "missing",
    ],
)
def test_an_answer_that_does_not_fit_is_refused(broken: dict[str, object]) -> None:
    task = _task()
    request = build_request(task, model="m")
    answers = {**FULL, **broken}
    answers = {key: value for key, value in answers.items() if value is not None}

    with pytest.raises(WireAnswerError):
        parse_response(_answers(**answers), task, request.questions)


def test_an_unreadable_body_is_refused() -> None:
    task = _task()

    with pytest.raises(WireAnswerError):
        parse_response(b"<html>", task, build_request(task, model="m").questions)

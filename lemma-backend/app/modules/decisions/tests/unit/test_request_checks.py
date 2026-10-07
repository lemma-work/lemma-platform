"""A request is checked whole, refused rather than truncated, and its answers
schema lets a provider say "can't tell"."""

from __future__ import annotations

import pytest

from app.modules.decisions.domain.answers import answer_problems, answer_schema
from app.modules.decisions.domain.errors import DecisionInvalidError
from app.modules.decisions.domain.request import (
    MAX_EVIDENCE_BYTES,
    MAX_EXAMPLE_BYTES,
    DecisionExample,
    DecisionRequest,
    build_task,
)

pytestmark = pytest.mark.unit

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["billing", "bug"],
            "description": "What is it about?",
        },
        "severity": {
            "type": "integer",
            "minimum": 1,
            "maximum": 3,
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


def _request(**overrides: object) -> DecisionRequest:
    fields: dict[str, object] = {
        "instruction": "Triage this email.",
        "evidence": {"subject": "Refund"},
        "schema": SCHEMA,
    }
    fields.update(overrides)
    return DecisionRequest(**fields)  # type: ignore[arg-type]


def test_json_evidence_is_rendered_compactly_and_kept_as_sent() -> None:
    task = build_task(_request(evidence={"subject": "Refund", "n": 2}))

    assert task.evidence_text == '{"subject":"Refund","n":2}'
    assert task.evidence == {"subject": "Refund", "n": 2}


def test_oversized_evidence_is_refused_not_truncated() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(_request(evidence="x" * (MAX_EVIDENCE_BYTES + 1)))

    assert raised.value.code == "DECISION_INPUT_TOO_LARGE"
    assert raised.value.problems[0]["path"] == "evidence"


def test_schema_instruction_and_evidence_problems_come_back_together() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(
            _request(
                instruction="   ",
                evidence=None,
                schema={"type": "object", "properties": {"q": {"type": "string"}}},
            )
        )

    paths = {problem["path"] for problem in raised.value.problems}
    assert {"instruction", "evidence", "schema.properties.q"} <= paths
    assert raised.value.code == "DECISION_INVALID_REQUEST"


def test_examples_may_answer_some_questions_or_none() -> None:
    task = build_task(
        _request(
            examples=[
                DecisionExample(
                    evidence="double charged", answers={"category": "billing"}
                ),
                DecisionExample(evidence="no idea", answers={"severity": None}),
            ]
        )
    )

    assert [example.answers for example in task.examples] == [
        {"category": "billing"},
        {"severity": None},
    ]


def test_an_example_answer_outside_the_question_is_refused() -> None:
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(
            _request(
                examples=[
                    DecisionExample(evidence="x", answers={"category": "refunds"}),
                    DecisionExample(evidence="y", answers={"unknown": "z"}),
                ]
            )
        )

    paths = [problem["path"] for problem in raised.value.problems]
    assert paths == ["examples[0].answers", "examples[1].answers"]


def test_examples_are_bounded_in_total() -> None:
    half = "x" * (MAX_EXAMPLE_BYTES // 2 + 1)
    with pytest.raises(DecisionInvalidError) as raised:
        build_task(
            _request(
                examples=[
                    DecisionExample(evidence=half, answers={}),
                    DecisionExample(evidence=half, answers={}),
                ]
            )
        )

    assert raised.value.code == "DECISION_INPUT_TOO_LARGE"


def test_every_answer_may_be_null_and_none_may_be_missing() -> None:
    task = build_task(_request())
    unsure = {"category": None, "severity": None, "labels": None}

    assert answer_problems(task.schema, unsure) == []
    assert answer_problems(task.schema, {"category": "bug"})  # two missing
    assert answer_problems(task.schema, {**unsure, "extra": True}), (
        "an answer to a question nobody asked"
    )


def test_answers_must_be_values_the_question_offers() -> None:
    task = build_task(_request())

    assert (
        answer_problems(
            task.schema, {"category": "billing", "severity": 3, "labels": ["vip"]}
        )
        == []
    )
    for wrong in (
        {"category": "refunds", "severity": 1, "labels": []},
        {"category": "bug", "severity": 4, "labels": []},
        {"category": "bug", "severity": True, "labels": []},
        {"category": "bug", "severity": 1, "labels": ["vip", "vip"]},
    ):
        assert answer_problems(task.schema, wrong), wrong


def test_scales_are_offered_as_levels_with_the_value_branch_first() -> None:
    """A stand-in model that answers an enum's first entry, or 0 for a bare
    integer, must still produce a valid answer for a scale starting at 1."""
    schema = answer_schema(build_task(_request()).schema)
    severity = schema["properties"]["severity"]  # type: ignore[index]

    assert severity["anyOf"][0] == {"type": "integer", "enum": [1, 2, 3]}
    assert severity["anyOf"][1] == {"type": "null"}
    assert schema["required"] == ["category", "severity", "labels"]

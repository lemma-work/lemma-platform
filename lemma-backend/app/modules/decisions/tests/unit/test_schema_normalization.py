"""The closed schema subset: what is accepted, and that the rest is refused."""

from __future__ import annotations

import pytest

from app.modules.decisions.domain.errors import DecisionInvalidError
from app.modules.decisions.domain.questions import (
    MAX_QUESTIONS,
    normalize_schema,
    parse_schema,
)

pytestmark = pytest.mark.unit


def _schema(**properties: object) -> dict[str, object]:
    return {"type": "object", "properties": properties}


def _paths(raw: object) -> set[str]:
    _, problems = parse_schema(raw)
    return {problem["path"] for problem in problems}


def test_every_kind_is_read_into_a_question() -> None:
    schema = normalize_schema(
        _schema(
            category={
                "type": "string",
                "enum": ["billing", "bug"],
                "description": "What is it about?",
            },
            urgent={"type": "boolean", "description": "Reply today?"},
            severity={
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "description": "How bad?",
            },
            labels={
                "type": "array",
                "items": {"type": "string", "enum": ["vip", "refund"]},
                "uniqueItems": True,
                "description": "Which apply?",
            },
        )
    )

    assert [(q.key, q.kind, q.values) for q in schema.questions] == [
        ("category", "choice", ("billing", "bug")),
        ("urgent", "boolean", ()),
        ("severity", "scale", (1, 2, 3, 4, 5)),
        ("labels", "multi_choice", ("vip", "refund")),
    ]


def test_described_options_keep_their_descriptions() -> None:
    schema = normalize_schema(
        _schema(
            category={
                "type": "string",
                "oneOf": [
                    {"const": "billing", "description": "Invoices and refunds"},
                    {"const": "other"},
                ],
                "description": "What is it about?",
            }
        )
    )

    options = schema.questions[0].options
    assert [(o.value, o.description) for o in options] == [
        ("billing", "Invoices and refunds"),
        ("other", None),
    ]


def test_described_scale_levels_are_sorted_lowest_first() -> None:
    schema = normalize_schema(
        _schema(
            effort={
                "type": "integer",
                "oneOf": [
                    {"const": 3, "description": "A day"},
                    {"const": 1, "description": "Minutes"},
                    {"const": 2, "description": "An hour"},
                ],
                "description": "How much work?",
            }
        )
    )

    assert schema.questions[0].values == (1, 2, 3)


def test_title_and_schema_annotations_are_tolerated() -> None:
    raw = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Triage",
        "type": "object",
        "additionalProperties": False,
        "required": ["urgent"],
        "properties": {"urgent": {"type": "boolean", "description": "Urgent?"}},
    }

    assert normalize_schema(raw).keys == ("urgent",)


@pytest.mark.parametrize(
    ("node", "path"),
    [
        ({"type": "string", "description": "Free text?"}, "schema.properties.q"),
        (
            {"type": "number", "minimum": 0, "maximum": 1, "description": "x"},
            "schema.properties.q.type",
        ),
        (
            {"type": "string", "enum": ["a", "b"], "pattern": "x", "description": "x"},
            "schema.properties.q.pattern",
        ),
        (
            {"type": "integer", "minimum": 0, "maximum": 1000, "description": "x"},
            "schema.properties.q",
        ),
        (
            {"type": "object", "properties": {}, "description": "nested"},
            "schema.properties.q.type",
        ),
        ({"type": "string", "enum": ["a", "b"]}, "schema.properties.q.description"),
        (
            {"type": "string", "enum": ["a", "a"], "description": "x"},
            "schema.properties.q",
        ),
        (
            {
                "type": "array",
                "items": {"type": "string", "enum": ["a", "b"]},
                "description": "x",
            },
            "schema.properties.q.uniqueItems",
        ),
    ],
    ids=[
        "free-text",
        "open-number",
        "unknown-keyword",
        "scale-too-long",
        "nested-object",
        "no-question-text",
        "duplicate-options",
        "multi-without-unique",
    ],
)
def test_anything_outside_the_subset_is_refused(node: object, path: str) -> None:
    assert path in _paths(_schema(q=node))


def test_every_problem_is_reported_at_once() -> None:
    raw = _schema(
        a={"type": "string", "description": "free"},
        b={"type": "boolean"},
    )

    with pytest.raises(DecisionInvalidError) as raised:
        normalize_schema(raw)

    assert {p["path"] for p in raised.value.problems} == {
        "schema.properties.a",
        "schema.properties.b.description",
    }
    assert raised.value.status_code == 422


def test_keys_are_safe_identifiers() -> None:
    assert "schema.properties.Bad Key" in _paths(
        _schema(**{"Bad Key": {"type": "boolean", "description": "x"}})
    )


def test_the_root_must_be_a_closed_object_of_questions() -> None:
    assert "schema" in _paths(["not", "an", "object"])
    assert "schema.properties" in _paths({"type": "object", "properties": {}})
    assert "schema.additionalProperties" in _paths(
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {"q": {"type": "boolean", "description": "x"}},
        }
    )


def test_question_count_is_bounded() -> None:
    raw = _schema(
        **{
            f"q{index}": {"type": "boolean", "description": "x"}
            for index in range(MAX_QUESTIONS + 1)
        }
    )

    assert "schema.properties" in _paths(raw)

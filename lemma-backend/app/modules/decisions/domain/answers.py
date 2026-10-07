"""What a decision answers, and the one check every provider's answer passes.

`value` is `None` when the provider could not tell -- the evidence does not
support an answer. That is an answer: the caller routes it, asks a person, or
skips. A provider that could not answer *at all* raises instead, so "unsure" and
"failed" can never be confused downstream.

`confidence` is the provider's own measure of the chosen value, when it has one.
A language model's self-reported number is not one, so the model provider
leaves it `None` rather than invent a figure callers would threshold on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.modules.decisions.domain.questions import DecisionSchema, Question

#: A choice, the choices of a multi-choice, yes or no, or a scale's level.
AnswerValue = str | int | bool | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Answer:
    value: AnswerValue | None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class DecisionUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class DecisionResult:
    answers: Mapping[str, Answer]
    #: The provider that answered, as configured: `model` or `typesafe`.
    provider: str
    #: The model that actually ran, which may differ from the one asked for.
    model: str | None
    usage: DecisionUsage = field(default_factory=DecisionUsage)


def value_schema(question: Question) -> dict[str, object]:
    """The JSON Schema of one question's answer, without its null branch."""
    values = list(question.values)
    match question.kind:
        case "choice":
            return {"type": "string", "enum": values}
        case "multi_choice":
            return {
                "type": "array",
                "items": {"type": "string", "enum": values},
                "uniqueItems": True,
            }
        case "boolean":
            return {"type": "boolean"}
        case "scale":
            # Levels as an enum, not a range: the e2e stand-in model answers
            # with an enum's first entry, and with 0 for a bare integer, which a
            # scale starting at 1 would reject.
            return {"type": "integer", "enum": values}


def answer_schema(schema: DecisionSchema) -> dict[str, object]:
    """The object a provider returns: every question, each answered or null.

    The value branch comes first in each `anyOf`, for the same stand-in.
    """
    return {
        "type": "object",
        "properties": {
            question.key: {
                "description": question.text,
                "anyOf": [value_schema(question), {"type": "null"}],
            }
            for question in schema.questions
        },
        "required": list(schema.keys),
        "additionalProperties": False,
    }


def answer_problems(
    schema: DecisionSchema, answers: object, *, partial: bool = False
) -> list[str]:
    """What is wrong with `answers` for `schema`, empty when nothing is.

    `partial` allows questions to be left out, which an example may do.
    """
    from jsonschema import Draft202012Validator

    expected = answer_schema(schema)
    if partial:
        expected = {**expected, "required": []}
    validator = Draft202012Validator(expected)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or 'answers'}: "
        f"{error.message}"
        for error in validator.iter_errors(answers)
    ]


def to_value(question: Question, raw: object) -> AnswerValue | None:
    """One checked JSON answer as an `AnswerValue`; lists become tuples."""
    if raw is None:
        return None
    if question.kind == "multi_choice" and isinstance(raw, list):
        order = {value: index for index, value in enumerate(question.values)}
        return tuple(sorted((str(item) for item in raw), key=lambda v: order[v]))
    if isinstance(raw, (str, bool, int)):
        return raw
    raise ValueError(f"unchecked answer for {question.key}")


def to_json(value: AnswerValue | None) -> object:
    return list(value) if isinstance(value, tuple) else value

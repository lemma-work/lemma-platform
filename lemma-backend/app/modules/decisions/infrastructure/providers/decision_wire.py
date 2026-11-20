"""The request and response shapes of the decision APIs, and nothing else.

Typesafe System One and Google's Decisions API (`gemini-decision-1`) speak the
same language with different accents -- a `WireDialect` each. Pure, so the
mapping can be tested without a network. Both know three question types:
`choice` (one of named criteria), `noul` (the probability that something is
true) and `score` (a point on an ordered list of criteria). Every decision kind
maps onto them:

- a choice is a `choice`, one criterion per option;
- yes or no is a `noul`;
- a scale is a `score`, one criterion per level, lowest first;
- a multi-choice is one `noul` per option, all in the same request.

Confidence is the probability of the value chosen, read from the distribution
returned. The APIs' own `confidence` field measures how concentrated the
whole distribution is -- a different number -- so it is not passed through.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
)

from app.modules.decisions.domain.answers import Answer, AnswerValue
from app.modules.decisions.domain.questions import Option, Question
from app.modules.decisions.domain.request import DecisionTask


@dataclass(frozen=True, slots=True)
class WireDialect:
    """Where the two decision APIs differ on the wire."""

    #: Between a multi-choice question's key and an option's index. A question
    #: key is lowercase letters, digits and `_` only, so neither separator can
    #: appear in one, and `key<sep>index` is unique across plain and
    #: multi-choice questions alike -- which `key__value` was not (`a` with
    #: option `b__c` met `a__b` with `c`).
    multi_separator: str
    #: The body field the evidence goes in.
    evidence_field: str
    #: A yes-or-no question's two criteria keys.
    noul_criteria: tuple[str, str]
    #: Worked examples as a structured criterion, or written into its text.
    structured_examples: bool


#: Typesafe System One.
TYPESAFE = WireDialect(
    multi_separator=".",
    evidence_field="state",
    noul_criteria=("true", "false"),
    structured_examples=True,
)
#: Google's Decisions API. Its keys allow `[A-Za-z0-9_-]` only, so no dot, and
#: its criteria are text.
GEMINI = WireDialect(
    multi_separator="-",
    evidence_field="input",
    noul_criteria=("true_description", "false_description"),
    structured_examples=False,
)
#: Worked examples sent per option. More is mostly repetition, and every one is
#: input the request pays for.
_EXAMPLES_PER_OPTION = 8


class _WireAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choice: str | None = None
    noul: float | None = None
    score: float | None = None
    probabilities: dict[str, float] | None = None


class _WireUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int | None = Field(
        default=None,
        validation_alias=AliasChoices("input_tokens", "total_input_tokens"),
    )


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    answers: dict[str, _WireAnswer]
    usage: _WireUsage | None = None


@dataclass(frozen=True, slots=True)
class WireQuestion:
    """Which decision question a wire question answers, and for a multi-choice,
    which of its options."""

    key: str
    option: str | None = None


@dataclass(frozen=True, slots=True)
class WireRequest:
    body: dict[str, JsonValue]
    questions: Mapping[str, WireQuestion]


class WireAnswerError(ValueError):
    """The provider answered in a shape that does not fit the question."""


def build_request(
    task: DecisionTask, *, model: str, dialect: WireDialect = TYPESAFE
) -> WireRequest:
    questions: dict[str, JsonValue] = {}
    mapping: dict[str, WireQuestion] = {}
    for question in task.schema.questions:
        instructions = f"{task.instruction}\n\nQuestion: {question.text}"
        examples = _examples_by_value(task, question.key)
        if question.kind == "multi_choice":
            for index, option in enumerate(question.options):
                wire_key = f"{question.key}{dialect.multi_separator}{index}"
                questions[wire_key] = _option_noul(
                    instructions, option, examples, dialect
                )
                mapping[wire_key] = WireQuestion(question.key, str(option.value))
            continue
        questions[question.key] = _single(question, instructions, examples, dialect)
        mapping[question.key] = WireQuestion(question.key)
    return WireRequest(
        body={
            "model": model,
            dialect.evidence_field: _evidence(task.evidence, dialect),
            "questions": questions,
        },
        questions=mapping,
    )


def _evidence(evidence: JsonValue, dialect: WireDialect) -> JsonValue:
    if dialect is TYPESAFE:
        return evidence if isinstance(evidence, dict) else {"evidence": evidence}
    # Gemini's input is text (or content blocks): structured evidence goes as
    # the JSON it is, which the model reads like any document.
    return evidence if isinstance(evidence, str) else json.dumps(evidence)


def _single(
    question: Question,
    instructions: str,
    examples: Mapping[str, list[str]],
    dialect: WireDialect,
) -> JsonValue:
    match question.kind:
        case "choice":
            return {
                "type": "choice",
                "instructions": instructions,
                "criteria": {
                    str(option.value): _criterion(
                        option, examples.get(str(option.value)), dialect
                    )
                    for option in question.options
                },
            }
        case "boolean":
            wire: dict[str, JsonValue] = {"type": "noul", "instructions": instructions}
            if examples:
                true_key, false_key = dialect.noul_criteria
                wire["criteria"] = {
                    true_key: _criterion(Option("Yes."), examples.get("true"), dialect),
                    false_key: _criterion(
                        Option("No."), examples.get("false"), dialect
                    ),
                }
            return wire
        case _:
            return {
                "type": "score",
                "instructions": instructions,
                "criteria": [
                    _criterion(option, examples.get(str(option.value)), dialect)
                    for option in question.options
                ],
            }


def _option_noul(
    instructions: str,
    option: Option,
    examples: Mapping[str, list[str]],
    dialect: WireDialect,
) -> JsonValue:
    described = option.description or str(option.value)
    true_key, false_key = dialect.noul_criteria
    return {
        "type": "noul",
        "instructions": f"{instructions}\n\nDoes this option apply? {described}",
        "criteria": {
            true_key: _criterion(option, examples.get(str(option.value)), dialect),
            false_key: "The option does not apply.",
        },
    }


def _criterion(
    option: Option, examples: list[str] | None, dialect: WireDialect
) -> JsonValue:
    """A criterion as a plain description, or with examples when there are some."""
    what = option.description or str(option.value)
    if not examples:
        return what
    chosen = list(examples[:_EXAMPLES_PER_OPTION])
    if dialect.structured_examples:
        return {"what": what, "examples": chosen}
    listed = "\n".join(f"- {example}" for example in chosen)
    return f"{what}\nCases answered this way:\n{listed}"


def _examples_by_value(task: DecisionTask, key: str) -> dict[str, list[str]]:
    """Each example's evidence, filed under the answer it was given for `key`."""
    by_value: dict[str, list[str]] = {}
    for example in task.examples:
        value = example.answers.get(key)
        values = value if isinstance(value, tuple) else (value,)
        for single in values:
            if single is None:
                continue
            label = str(single).lower() if isinstance(single, bool) else str(single)
            by_value.setdefault(label, []).append(example.evidence)
    return by_value


def parse_response(
    content: bytes, task: DecisionTask, questions: Mapping[str, WireQuestion]
) -> tuple[dict[str, Answer], str | None, int | None]:
    """Answers per decision question, the model that ran, and input tokens.

    Raises `WireAnswerError` for anything that does not fit: a choice that was
    not offered, a probability outside [0, 1], a question left unanswered.
    """
    try:
        wire = WireResponse.model_validate_json(content)
    except ValidationError as exc:
        raise WireAnswerError("unreadable response") from exc
    multi: dict[str, dict[str, float]] = {}
    answers: dict[str, Answer] = {}
    for wire_key, target in questions.items():
        found = wire.answers.get(wire_key)
        if found is None:
            raise WireAnswerError(f"no answer for {wire_key}")
        if target.option is not None:
            multi.setdefault(target.key, {})[target.option] = _probability(found.noul)
            continue
        question = task.schema.question(target.key)
        if question is None:
            raise WireAnswerError(f"unknown question {target.key}")
        answers[target.key] = _single_answer(question, found)
    for key, chances in multi.items():
        question = task.schema.question(key)
        if question is None:
            raise WireAnswerError(f"unknown question {key}")
        answers[key] = _multi_answer(question, chances)
    input_tokens = wire.usage.input_tokens if wire.usage else None
    return answers, wire.model, input_tokens


def _single_answer(question: Question, wire: _WireAnswer) -> Answer:
    match question.kind:
        case "choice":
            allowed = {str(value) for value in question.values}
            if wire.choice is None or wire.choice not in allowed:
                raise WireAnswerError(f"{question.key}: choice not offered")
            return Answer(wire.choice, _chance(wire.probabilities, wire.choice))
        case "boolean":
            p_true = _probability(wire.noul)
            return Answer(p_true >= 0.5, max(p_true, 1 - p_true))
        case _:
            index, chance = _level(wire, len(question.options))
            value: AnswerValue = question.options[index].value
            return Answer(value, chance)


def _multi_answer(question: Question, chances: Mapping[str, float]) -> Answer:
    chosen = tuple(
        str(value) for value in question.values if chances.get(str(value), 0.0) >= 0.5
    )
    certainty = min(max(p, 1 - p) for p in chances.values())
    return Answer(chosen, certainty)


def _level(wire: _WireAnswer, count: int) -> tuple[int, float | None]:
    """The most probable level, else the reported score rounded to one."""
    if wire.probabilities:
        candidates = {
            int(level): _probability(p)
            for level, p in wire.probabilities.items()
            if level.isdigit() and int(level) < count
        }
        if candidates:
            index = max(candidates, key=lambda level: candidates[level])
            return index, candidates[index]
    if wire.score is not None and math.isfinite(wire.score):
        index = round(wire.score)
        if 0 <= index < count:
            return index, None
    raise WireAnswerError("score outside the scale")


def _chance(probabilities: Mapping[str, float] | None, value: str) -> float | None:
    if not probabilities or value not in probabilities:
        return None
    return _probability(probabilities[value])


def _probability(value: float | None) -> float:
    if value is None or not math.isfinite(value) or not 0 <= value <= 1:
        raise WireAnswerError("probability outside [0, 1]")
    return value

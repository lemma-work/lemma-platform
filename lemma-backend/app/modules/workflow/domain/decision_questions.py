"""A decision node's question: checking it when saved, routing its answer.

The question itself is asked by the decisions module, outside the run's
transaction. What lives here is what the workflow owns either side of that:
whether a question can be routed at all, which answers it can come back with,
and which node an answer sends the run to.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.modules.decisions.contracts.decide import decision_schema_problems
from app.modules.workflow.domain.nodes.decision import (
    ANSWER_KEY,
    DecisionQuestion,
    QuestionAnswer,
)

_ASKED_PATH = f"schema.properties.{ANSWER_KEY}"
#: The question kinds a node can route on: one value per answer.
_ROUTABLE_TYPES = frozenset({"string", "boolean", "integer"})


class DecisionAskExample(BaseModel):
    evidence: JsonValue
    answers: dict[str, QuestionAnswer | None]


class DecisionAsk(BaseModel):
    """What a DECISION wait asks, kept on the wait row until it is answered.

    Resolved when the run reaches the node, so the question asked is the one
    the run saw -- even if the flow is edited while the answer is pending.
    Routing the answer is the other way round: it reads the flow as it is.
    """

    model_config = ConfigDict(populate_by_name=True)

    node_id: str
    instruction: str
    evidence: JsonValue
    schema_: dict[str, JsonValue] = Field(alias="schema")
    examples: list[DecisionAskExample] = Field(default_factory=list)

    @classmethod
    def for_question(
        cls, node_id: str, question: DecisionQuestion, evidence: JsonValue
    ) -> DecisionAsk:
        return cls(
            node_id=node_id,
            instruction=question.instruction,
            evidence=evidence,
            schema=question.asked_schema(),
            examples=[
                DecisionAskExample(
                    evidence=example.evidence,
                    answers={ANSWER_KEY: example.answer},
                )
                for example in question.examples
            ],
        )

    def to_payload(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", by_alias=True)

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> DecisionAsk:
        return cls.model_validate(payload)


def route_key(value: QuestionAnswer) -> str:
    """An answer as a `routes` key: `true`/`false`, `3`, or the option itself."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def answer_values(answer: Mapping[str, JsonValue]) -> tuple[QuestionAnswer, ...]:
    """Every value a checked single-value question can be answered with."""
    match answer.get("type"):
        case "boolean":
            return (True, False)
        case "string":
            enum = answer.get("enum")
            if isinstance(enum, list):
                return tuple(value for value in enum if isinstance(value, str))
            return tuple(value for value in _consts(answer) if isinstance(value, str))
        case "integer":
            low, high = answer.get("minimum"), answer.get("maximum")
            if isinstance(low, int) and isinstance(high, int):
                return tuple(range(low, high + 1))
            return tuple(
                value
                for value in _consts(answer)
                if isinstance(value, int) and not isinstance(value, bool)
            )
    return ()


def _consts(answer: Mapping[str, JsonValue]) -> list[JsonValue]:
    one_of = answer.get("oneOf")
    if not isinstance(one_of, list):
        return []
    return [entry.get("const") for entry in one_of if isinstance(entry, dict)]


def question_issues(
    node_id: str,
    question: DecisionQuestion,
    *,
    node_ids: Collection[str],
    has_default_edge: bool,
) -> list[str]:
    """Everything that would stop this question from being asked or routed."""
    prefix = f"decision '{node_id}'"
    issues = [
        f"{prefix} {_relabel(problem)}"
        for problem in decision_schema_problems(question.asked_schema())
    ]
    if issues:
        return issues
    if question.answer.get("type") not in _ROUTABLE_TYPES:
        return [
            (
                f"{prefix} asks a multi-choice question, which cannot be routed; "
                "ask a choice, yes or no, or a scale"
            )
        ]
    values = answer_values(question.answer)
    keys = [route_key(value) for value in values]
    issues.extend(_route_issues(prefix, question, keys, node_ids))
    issues.extend(_example_issues(prefix, question, values))
    if not has_default_edge:
        issues.extend(_unrouted_issues(prefix, question, keys))
    return issues


def _relabel(problem: str) -> str:
    """A decisions-contract problem, pointed at the node's own field."""
    if problem.startswith(_ASKED_PATH):
        return f"question.answer{problem.removeprefix(_ASKED_PATH)}"
    return f"question.answer: {problem}"


def _route_issues(
    prefix: str,
    question: DecisionQuestion,
    keys: list[str],
    node_ids: Collection[str],
) -> list[str]:
    issues: list[str] = []
    for key, target in question.routes.items():
        if key not in keys:
            issues.append(
                f"{prefix} routes '{key}', which is not an answer to its question "
                f"(answers: {', '.join(keys)})"
            )
        if target not in node_ids:
            issues.append(f"{prefix} routes '{key}' to missing node '{target}'")
    unsure = question.unsure_next_node_id
    if unsure is not None and unsure not in node_ids:
        issues.append(f"{prefix} routes an unsure answer to missing node '{unsure}'")
    return issues


def _example_issues(
    prefix: str, question: DecisionQuestion, values: tuple[QuestionAnswer, ...]
) -> list[str]:
    allowed = {(type(value), value) for value in values}
    return [
        f"{prefix} example {index} answers {example.answer!r}, which is not an "
        "answer to its question"
        for index, example in enumerate(question.examples)
        if example.answer is not None
        and (type(example.answer), example.answer) not in allowed
    ]


def _unrouted_issues(
    prefix: str, question: DecisionQuestion, keys: list[str]
) -> list[str]:
    issues: list[str] = []
    unrouted = [key for key in keys if key not in question.routes]
    if unrouted:
        issues.append(
            f"{prefix} has no route for {', '.join(unrouted)} and no default edge"
        )
    if question.unsure_next_node_id is None:
        issues.append(f"{prefix} has no route for an unsure answer and no default edge")
    return issues


@dataclass(frozen=True, slots=True)
class DecisionRoute:
    """Where an answer sends the run -- `None` when nowhere does."""

    next_node_id: str | None
    #: Whether the answer counted as unsure: no value, or too little confidence.
    unsure: bool


def choose_route(
    question: DecisionQuestion,
    *,
    value: QuestionAnswer | None,
    confidence: float | None,
    default_next_node_id: str | None,
) -> DecisionRoute:
    """The node an answer sends the run to.

    A sure answer goes where its route says; an unsure one -- no value, or a
    confidence below `min_confidence` -- to `unsure_next_node_id`. Either falls
    back to the default edge. Nowhere at all means the run cannot go on, which
    the caller turns into a failure rather than a guess.
    """
    unsure = value is None or (
        question.min_confidence is not None
        and confidence is not None
        and confidence < question.min_confidence
    )
    if unsure:
        target = question.unsure_next_node_id
    else:
        assert value is not None
        target = question.routes.get(route_key(value))
    return DecisionRoute(next_node_id=target or default_next_node_id, unsure=unsure)

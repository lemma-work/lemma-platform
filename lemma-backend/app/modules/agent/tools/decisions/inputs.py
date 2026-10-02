"""The agent's arguments, checked and turned into what the contract takes.

Everything here refuses with `InputRefused`, whose message goes back to the
model: which argument, what is wrong with it, and what to pass instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from uuid import UUID

from pydantic import ValidationError

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.decisions.contract import (
    inline_definition,
    parse_definition,
    parse_options,
)
from app.modules.agent.tools.decisions.models import InputRefused
from app.modules.agent.tools.decisions.seams import AnswerValue

if TYPE_CHECKING:
    from app.modules.decisions.contracts.decide import DeciderDefinition, Option

#: How an inline judgement is named back to the agent.
INLINE = "inline"

#: Problems listed from one invalid argument; the model fixes these and learns
#: of any others on its next try.
_LISTED_PROBLEMS = 8


def invalid(argument: str, exc: ValidationError) -> InputRefused:
    """The agent's own input, said back as what is wrong with which part of it."""
    problems = [
        f"{'.'.join(str(part) for part in error['loc']) or argument}: {error['msg']}"
        for error in exc.errors()[:_LISTED_PROBLEMS]
    ]
    return InputRefused(f"`{argument}` is not valid: {'; '.join(problems)}.")


@dataclass(frozen=True, slots=True)
class Judgement:
    """What is asked: a named decider, or a definition passed with the call."""

    decider: str | None
    definition: DeciderDefinition | None

    @property
    def label(self) -> str:
        return self.decider or INLINE


def judgement(
    *,
    decider: str | None,
    questions: JsonObject | str | None,
    definition: JsonObject | str | None,
    guidance: str | None = None,
) -> Judgement:
    """Exactly one of a saved decider, one-off questions, or a draft definition."""
    given = [value for value in (decider, questions, definition) if value is not None]
    if len(given) != 1:
        raise InputRefused(
            "Name the judgement one way: `decider` for a saved one, `questions` "
            "for a one-off, or `definition` for an unsaved draft."
        )
    if guidance and questions is None:
        raise InputRefused(
            "`guidance` goes with `questions`; a saved decider or a definition "
            "carries its own."
        )
    if decider is not None:
        return Judgement(decider.strip(), None)
    if questions is not None:
        try:
            return Judgement(
                None, inline_definition(cast(JsonObject, questions), guidance)
            )
        except ValidationError as exc:
            raise invalid("questions", exc) from exc
    return Judgement(None, definition_of(cast(JsonObject, definition)))


def definition_of(raw: JsonObject) -> DeciderDefinition:
    try:
        return parse_definition(raw)
    except ValidationError as exc:
        raise invalid("definition", exc) from exc


def call_options(raw: JsonObject | str | None) -> dict[str, dict[str, Option]]:
    if not raw:
        return {}
    try:
        return parse_options(cast(JsonObject, raw))
    except ValidationError as exc:
        raise invalid("options", exc) from exc


def question_types(definition: DeciderDefinition | None) -> dict[str, str]:
    """Question key -> its type, where the definition is at hand."""
    if definition is None:
        return {}
    return {key: question.type for key, question in definition.questions.items()}


def expected_fields(
    raw: JsonObject | str | None, definition: DeciderDefinition | None
) -> dict[str, str]:
    """Question -> the row field holding the answer a person gave to it."""
    fields: dict[str, str] = {}
    for question, field in cast(JsonObject, raw or {}).items():
        if not isinstance(field, str) or not field.strip():
            raise InputRefused(
                f"`expected.{question}` must name the field that holds the "
                'known answer, e.g. "label".'
            )
        fields[question] = field.strip()
    known = question_types(definition)
    unknown = sorted(set(fields) - set(known)) if known else []
    if unknown:
        raise InputRefused(
            f"`expected` names {', '.join(unknown)}, which the decider does not "
            f"ask. Its questions: {', '.join(sorted(known))}."
        )
    return fields


def answers_of(raw: JsonObject | str) -> dict[str, AnswerValue]:
    """The answers an agent gives a decision, each of a shape an answer can have."""
    answers: dict[str, AnswerValue] = {}
    for question, value in cast(JsonObject, raw).items():
        if isinstance(value, bool | int | str):
            answers[question] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            answers[question] = [str(item) for item in value]
        else:
            raise InputRefused(
                f"The answer to `{question}` must be an option key, a list of "
                "them, true or false, or a level's index."
            )
    if not answers:
        raise InputRefused("Give at least one question's answer in `answers`.")
    return answers


def decision_id_of(raw: str) -> UUID:
    try:
        return UUID(raw.strip())
    except ValueError as exc:
        raise InputRefused(
            f"`decision_id` {raw!r} is not a decision id; pass the `decision_id` "
            "that decide returned."
        ) from exc

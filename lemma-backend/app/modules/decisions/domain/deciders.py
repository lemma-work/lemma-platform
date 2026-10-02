"""Deciders: a judgement worth naming, versioning and reusing.

A decider is a function you teach instead of write: a typed input (its input
view), a closed output (its questions), and a body made of guidance, rules and
the examples people give it.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.authorization.context import ResourceType
from app.core.domain.entity import Entity
from app.modules.decisions.domain.questions import (
    AnswerValue,
    ChoiceQuestion,
    MultiChoiceQuestion,
    Question,
    validate_answer,
)

DECIDER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
QUESTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_QUESTIONS = 16
MAX_RULES = 100


class Lane(StrEnum):
    """Who is waiting, which decides how long a rung may take.

    Interactive: a person is waiting in a conversation. Ambient: an event is
    being sorted and nobody is watching. Bulk: many rows at once, which yields
    to the other two.
    """

    INTERACTIVE = "interactive"
    AMBIENT = "ambient"
    BULK = "bulk"


class DeciderScope(StrEnum):
    SYSTEM = "system"
    POD = "pod"
    INLINE = "inline"


class InputView(BaseModel):
    """The only part of the state any engine sees.

    `fields` are top-level keys or dotted paths into an object state. Leaving
    them out passes the whole state. Either way the rendered view is cut at
    `max_chars`, and the engines are told it was.
    """

    model_config = ConfigDict(extra="forbid")

    fields: list[str] | None = Field(default=None, max_length=64)
    max_chars: int = Field(default=8000, ge=200, le=60000)


class Rule(BaseModel):
    """A deterministic answer, tried before any engine.

    Either `when`, a JMESPath expression over the rendered state that answers
    when truthy, or `phrases`, exact matches against the text at `field` after
    lowercasing, collapsing whitespace and dropping trailing punctuation.
    """

    model_config = ConfigDict(extra="forbid")

    when: str | None = Field(default=None, max_length=2000)
    phrases: list[str] | None = Field(default=None, max_length=500)
    field: str = Field(default="text", max_length=200)
    answer: dict[str, AnswerValue] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_matcher(self) -> Rule:
        if (self.when is None) == (self.phrases is None):
            raise ValueError("a rule needs exactly one of `when` or `phrases`")
        return self


class Policy(BaseModel):
    """What each rung may answer, and when a rung passes a question on."""

    model_config = ConfigDict(extra="forbid")

    lane: Lane = Lane.AMBIENT
    escalate_to_model: bool = Field(
        default=True,
        description=(
            "Ask the model when System One abstains. Off, a System One "
            "abstention leaves the question open."
        ),
    )
    abstain_below: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description=(
            "System One's confidence below which a choice or scale answer is "
            "passed on rather than taken."
        ),
    )
    yes_no_band: tuple[float, float] = Field(
        default=(0.35, 0.65),
        description=(
            "Probabilities of yes inside this band are passed on rather than "
            "taken, for yes/no and each option of a multi-choice."
        ),
    )
    rules_only: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Per question, answers only the rules rung may give. An engine that "
            "gives one has not answered."
        ),
    )
    require_confidence: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description=(
            "Per question and answer, the System One confidence an engine "
            "answer needs to stand. A rung that reports no confidence never "
            "meets it."
        ),
    )

    @field_validator("yes_no_band")
    @classmethod
    def _ordered_band(cls, value: tuple[float, float]) -> tuple[float, float]:
        low, high = value
        if not 0 <= low <= 0.5 <= high <= 1:
            raise ValueError("the band must contain 0.5 and lie within 0 and 1")
        return value


class DeciderDefinition(BaseModel):
    """What a decider version is: every field an engine or a rule reads."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=2000)
    guidance: str | None = Field(default=None, max_length=8000)
    input: InputView = Field(default_factory=InputView)
    questions: dict[str, Question] = Field(min_length=1, max_length=MAX_QUESTIONS)
    rules: list[Rule] = Field(default_factory=list, max_length=MAX_RULES)
    policy: Policy = Field(default_factory=Policy)

    @model_validator(mode="after")
    def _consistent(self) -> DeciderDefinition:
        for key in self.questions:
            if not QUESTION_KEY_PATTERN.match(key):
                raise ValueError(
                    f"question key {key!r} must be lowercase letters, digits or '_', "
                    "starting with a letter"
                )
            if "__" in key:
                # System One asks a multi-choice question one option at a time
                # under `<question>__<option>`; a key holding `__` could be
                # mistaken for one of those.
                raise ValueError(f"question key {key!r} must not contain '__'")
        for index, rule in enumerate(self.rules):
            for key, value in rule.answer.items():
                question = self.questions.get(key)
                if question is None:
                    raise ValueError(f"rule {index} answers unknown question {key!r}")
                if _has_declared_options(question) or not isinstance(
                    question, ChoiceQuestion | MultiChoiceQuestion
                ):
                    try:
                        validate_answer(question, value)
                    except ValueError as exc:
                        raise ValueError(
                            f"rule {index}, question {key!r}: {exc}"
                        ) from exc
        for mapping_name, mapping in (
            ("rules_only", self.policy.rules_only),
            ("require_confidence", self.policy.require_confidence),
        ):
            for key in mapping:
                if key not in self.questions:
                    raise ValueError(
                        f"policy.{mapping_name} names unknown question {key!r}"
                    )
        return self


def _has_declared_options(question: Question) -> bool:
    return (
        isinstance(question, ChoiceQuestion | MultiChoiceQuestion)
        and question.options is not None
    )


def check_decider_name(name: str) -> str:
    if not DECIDER_NAME_PATTERN.match(name):
        raise ValueError(
            "a decider name is lowercase letters, digits, '_' or '-', starting with "
            "a letter or digit, at most 64 characters"
        )
    return name


class DeciderEntity(Entity):
    """A pod decider and its current definition."""

    resource_type: ClassVar[ResourceType] = ResourceType.DECIDER

    pod_id: UUID
    #: Who created it; None once that person's account is gone.
    user_id: UUID | None = None
    name: str
    visibility: str = "POD"
    version: int = 1
    definition: DeciderDefinition
    allowed_actions: list[str] = Field(default_factory=list)


class DeciderVersionEntity(BaseModel):
    """One saved version of a pod decider. Versions are never edited."""

    model_config = ConfigDict(from_attributes=True)

    decider_id: UUID
    version: int
    definition: DeciderDefinition
    created_by: UUID | None
    created_at: datetime


class ResolvedDecider(BaseModel):
    """What a decision is asked against: a definition and where it came from.

    `key` is what decisions and examples are filed under: the decider's name
    for a system or pod decider, a digest of the definition for an inline one.
    """

    model_config = ConfigDict(frozen=True)

    scope: DeciderScope
    key: str
    name: str | None
    version: int | None
    definition: DeciderDefinition

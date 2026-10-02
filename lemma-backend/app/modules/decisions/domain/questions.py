"""Questions: the type of a judgement.

A question is plain data a person, an LLM or code can write. It is closed: its
answer is always one of the things it declared, never text. That is what lets
the same question be answered by a rule, by System One, by a language model or
by a person, and what bounds what hostile input can make it say.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OPTION_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: The widest answer any question can have. A choice answers with an option key,
#: a multi-choice with a list of them, yes/no with a boolean, a scale with the
#: index of its level.
type AnswerValue = str | list[str] | bool | int

MAX_CHOICE_OPTIONS = 255
#: Multi-choice becomes one yes/no per option on System One, all in one
#: request, so the option count is also the request's question count.
MAX_MULTI_CHOICE_OPTIONS = 32
MIN_SCALE_LEVELS = 2
MAX_SCALE_LEVELS = 10


class Option(BaseModel):
    """What one option means, and optionally what it must not be used for."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=1000)
    not_for: str | None = Field(default=None, max_length=1000)
    examples: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("examples")
    @classmethod
    def _bounded_examples(cls, value: list[str]) -> list[str]:
        for example in value:
            if not example.strip() or len(example) > 500:
                raise ValueError(
                    "examples must be non-empty and at most 500 characters"
                )
        return value


def _normalize_options(value: object) -> object:
    """Accept `key: description` shorthand next to the full option object."""
    if not isinstance(value, dict):
        return value
    normalized: dict[object, object] = {}
    for key, option in value.items():
        normalized[key] = {"description": option} if isinstance(option, str) else option
    return normalized


def _check_option_keys(keys: list[str]) -> None:
    for key in keys:
        if not OPTION_KEY_PATTERN.match(key):
            raise ValueError(
                f"option key {key!r} must be lowercase letters, digits, '_' or '-', "
                "starting with a letter or digit, at most 64 characters"
            )


class _QuestionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)


class ChoiceQuestion(_QuestionBase):
    """Exactly one of the options.

    Declared options are the fixed ones; a caller adds its own with each call.
    That is how one definition answers "which of these?" over the person's
    pods, the open conversations or the options of a question already put to
    them, while its `none` or `not_an_answer` is always there to fall back on.
    """

    type: Literal["choice"] = "choice"
    options: dict[str, Option] | None = None
    fallback: str | None = Field(
        default=None,
        description="The option to answer when nothing clearly applies.",
    )

    @field_validator("options", mode="before")
    @classmethod
    def _shorthand(cls, value: object) -> object:
        return _normalize_options(value)

    @model_validator(mode="after")
    def _check(self) -> ChoiceQuestion:
        if self.options is not None:
            _check_option_count(len(self.options), 1, MAX_CHOICE_OPTIONS)
            _check_option_keys(list(self.options))
        if self.fallback is not None and (
            self.options is None or self.fallback not in self.options
        ):
            raise ValueError(
                f"fallback {self.fallback!r} must be one of the declared options"
            )
        return self


class MultiChoiceQuestion(_QuestionBase):
    """Any number of the options, including none."""

    type: Literal["multi_choice"] = "multi_choice"
    options: dict[str, Option] | None = None

    @field_validator("options", mode="before")
    @classmethod
    def _shorthand(cls, value: object) -> object:
        return _normalize_options(value)

    @model_validator(mode="after")
    def _check(self) -> MultiChoiceQuestion:
        if self.options is not None:
            _check_option_count(len(self.options), 1, MAX_MULTI_CHOICE_OPTIONS)
            _check_option_keys(list(self.options))
        return self


class YesNoQuestion(_QuestionBase):
    """True or false. `yes` and `no` optionally say what each means."""

    type: Literal["yes_no"] = "yes_no"
    yes: str | None = Field(default=None, max_length=1000)
    no: str | None = Field(default=None, max_length=1000)


class ScaleQuestion(_QuestionBase):
    """One of 2 to 10 ordered levels, lowest first. Answers with the level's index."""

    type: Literal["scale"] = "scale"
    levels: list[str] = Field(min_length=MIN_SCALE_LEVELS, max_length=MAX_SCALE_LEVELS)

    @field_validator("levels")
    @classmethod
    def _non_empty_levels(cls, value: list[str]) -> list[str]:
        if any(not level.strip() or len(level) > 500 for level in value):
            raise ValueError("levels must be non-empty and at most 500 characters")
        return value


#: A plain alias rather than a PEP 695 `type` statement: pydantic reads the
#: discriminator off the `Annotated` metadata, which it resolves reliably only
#: when the alias is an ordinary assignment.
Question = Annotated[
    ChoiceQuestion | MultiChoiceQuestion | YesNoQuestion | ScaleQuestion,
    Field(discriminator="type"),
]


def _check_option_count(count: int, minimum: int, maximum: int) -> None:
    if count < minimum or count > maximum:
        raise ValueError(f"a question needs between {minimum} and {maximum} options")


def with_options(question: Question, options: dict[str, Option] | None) -> Question:
    """The question with the caller's options added to its declared ones.

    A caller may not redefine a declared option: the fallback and every
    policy that names an option are written against the declared meaning.
    """
    if not options or not isinstance(question, ChoiceQuestion | MultiChoiceQuestion):
        return question
    declared = question.options or {}
    clashing = sorted(set(declared) & set(options))
    if clashing:
        raise ValueError(f"options {clashing} are already declared by the decider")
    merged = {key: option.model_dump() for key, option in declared.items()}
    merged.update({key: option.model_dump() for key, option in options.items()})
    return type(question).model_validate(
        {**question.model_dump(exclude={"options"}), "options": merged}
    )


def validate_answer(question: Question, value: AnswerValue) -> AnswerValue:
    """`value` if it is an answer `question` could give, else ValueError."""
    match question:
        case ChoiceQuestion(options=options):
            if not isinstance(value, str) or options is None or value not in options:
                raise ValueError("expected one of the question's option keys")
        case MultiChoiceQuestion(options=options):
            if (
                not isinstance(value, list)
                or options is None
                or any(item not in options for item in value)
            ):
                raise ValueError("expected a list of the question's option keys")
            return sorted(set(value))
        case YesNoQuestion():
            if not isinstance(value, bool):
                raise ValueError("expected true or false")
        case ScaleQuestion(levels=levels):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value < len(levels)
            ):
                raise ValueError(f"expected a level index from 0 to {len(levels) - 1}")
    return value


def require_answerable(question: Question) -> None:
    """Refuse a question that reached a rung with too few options to choose from."""
    if isinstance(question, ChoiceQuestion):
        _check_option_count(len(question.options or {}), 2, MAX_CHOICE_OPTIONS)
    elif isinstance(question, MultiChoiceQuestion):
        _check_option_count(len(question.options or {}), 1, MAX_MULTI_CHOICE_OPTIONS)

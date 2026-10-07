"""The questions a decision may ask: a closed subset of JSON Schema.

A caller describes what it wants answered as a flat JSON Schema object, one
property per question, the property's `description` being the question. Only
closed answers are accepted -- a choice, several choices, yes or no, a point on
a short scale -- because a closed answer can be checked against the question,
routed on, and counted, and an open one cannot. Free text is where evidence gets
copied into the output and carried into whatever reads it next; a number with no
fixed levels is extraction, not a decision.

Every provider must answer every kind below. That is what keeps provider choice
an operator's setting rather than something each caller has to think about.

Anything outside the subset is refused, not ignored. A keyword the subset does
not know (`minLength`, `pattern`, `$ref`, nesting) would be a constraint the
caller believes is enforced and is not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard

from app.modules.decisions.domain.errors import DecisionInvalidError

QuestionKind = Literal["choice", "multi_choice", "boolean", "scale"]
OptionValue = str | int

MAX_QUESTIONS = 16
MAX_OPTIONS = 64
MAX_SCALE_LEVELS = 11
MAX_OPTION_CHARS = 128
MAX_DESCRIPTION_CHARS = 1000
MAX_SCHEMA_BYTES = 16 * 1024

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ROOT_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "title", "$schema"}
    | {"description"}
)
_ANNOTATIONS = frozenset({"description", "title"})
_CHOICE_KEYWORDS = frozenset({"type", "enum", "oneOf"}) | _ANNOTATIONS
_MULTI_KEYWORDS = frozenset({"type", "items", "uniqueItems"}) | _ANNOTATIONS
_BOOLEAN_KEYWORDS = frozenset({"type"}) | _ANNOTATIONS
_SCALE_KEYWORDS = frozenset({"type", "minimum", "maximum", "oneOf"}) | _ANNOTATIONS
_LEVEL_KEYWORDS = frozenset({"const"}) | _ANNOTATIONS


@dataclass(frozen=True, slots=True)
class Option:
    """One allowed answer: a choice's value, or a scale's level."""

    value: OptionValue
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    text: str
    kind: QuestionKind
    #: Choices for `choice` and `multi_choice`, levels in ascending order for
    #: `scale`, empty for `boolean`.
    options: tuple[Option, ...] = ()

    @property
    def values(self) -> tuple[OptionValue, ...]:
        return tuple(option.value for option in self.options)


@dataclass(frozen=True, slots=True)
class DecisionSchema:
    questions: tuple[Question, ...]

    def question(self, key: str) -> Question | None:
        return next((q for q in self.questions if q.key == key), None)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(question.key for question in self.questions)


class _Problems:
    """Every problem with a schema, so the caller can fix them in one pass."""

    def __init__(self) -> None:
        self.found: list[dict[str, str]] = []

    def add(self, path: str, message: str) -> None:
        self.found.append({"path": path, "message": message})

    def unknown_keywords(
        self, path: str, node: Mapping[str, object], allowed: frozenset[str]
    ) -> None:
        for keyword in sorted(set(node) - allowed):
            self.add(f"{path}.{keyword}", "This keyword is not supported.")


def parse_schema(raw: object) -> tuple[DecisionSchema | None, list[dict[str, str]]]:
    """The questions `raw` asks, or `None` and every problem with it."""
    problems = _Problems()
    questions = _questions(raw, problems)
    if problems.found:
        return None, problems.found
    return DecisionSchema(questions=tuple(questions)), []


def normalize_schema(raw: object) -> DecisionSchema:
    """The questions `raw` asks, or `DecisionInvalidError` listing every problem."""
    schema, problems = parse_schema(raw)
    if schema is None:
        raise DecisionInvalidError(problems)
    return schema


def _questions(raw: object, problems: _Problems) -> list[Question]:
    if not isinstance(raw, Mapping):
        problems.add("schema", "Must be a JSON object.")
        return []
    if len(json.dumps(raw, separators=(",", ":")).encode()) > MAX_SCHEMA_BYTES:
        problems.add("schema", f"Must be at most {MAX_SCHEMA_BYTES} bytes.")
        return []
    problems.unknown_keywords("schema", raw, _ROOT_KEYWORDS)
    if raw.get("type") != "object":
        problems.add("schema.type", 'Must be "object".')
    if raw.get("additionalProperties", False) is not False:
        problems.add("schema.additionalProperties", "Only false is supported.")
    properties = raw.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        problems.add("schema.properties", "Must name at least one question.")
        return []
    if len(properties) > MAX_QUESTIONS:
        problems.add("schema.properties", f"At most {MAX_QUESTIONS} questions.")
    _check_required(raw.get("required"), properties, problems)
    parsed = [_question(str(key), node, problems) for key, node in properties.items()]
    return [question for question in parsed if question is not None]


def _check_required(
    required: object, properties: Mapping[str, object], problems: _Problems
) -> None:
    """`required` is tolerated, not meaningful: every question is answered."""
    if required is None:
        return
    if not isinstance(required, list) or any(
        not isinstance(key, str) or key not in properties for key in required
    ):
        problems.add("schema.required", "May only list the questions above.")


def _question(key: str, node: object, problems: _Problems) -> Question | None:
    path = f"schema.properties.{key}"
    if not _KEY.match(key):
        problems.add(path, "Keys are lowercase letters, digits and _, 1-64 long.")
        return None
    if not isinstance(node, Mapping):
        problems.add(path, "Must be a JSON object.")
        return None
    text = _text(node.get("description"), f"{path}.description", problems)
    match node.get("type"):
        case "string":
            problems.unknown_keywords(path, node, _CHOICE_KEYWORDS)
            options = _choices(node, path, problems)
            kind: QuestionKind = "choice"
        case "array":
            problems.unknown_keywords(path, node, _MULTI_KEYWORDS)
            options = _multi_choices(node, path, problems)
            kind = "multi_choice"
        case "boolean":
            problems.unknown_keywords(path, node, _BOOLEAN_KEYWORDS)
            options, kind = (), "boolean"
        case "integer":
            problems.unknown_keywords(path, node, _SCALE_KEYWORDS)
            options = _levels(node, path, problems)
            kind = "scale"
        case _:
            problems.add(
                f"{path}.type", 'Must be "string", "array", "boolean" or "integer".'
            )
            return None
    if text is None or options is None:
        return None
    return Question(key=key, text=text, kind=kind, options=options)


def _text(value: object, path: str, problems: _Problems) -> str | None:
    if not isinstance(value, str) or not value.strip():
        problems.add(path, "Every question needs a description: the question itself.")
        return None
    if len(value) > MAX_DESCRIPTION_CHARS:
        problems.add(path, f"At most {MAX_DESCRIPTION_CHARS} characters.")
        return None
    return value.strip()


def _choices(
    node: Mapping[str, object], path: str, problems: _Problems
) -> tuple[Option, ...] | None:
    """A choice's options, from `enum` or from `oneOf` entries carrying `const`."""
    enum, one_of = node.get("enum"), node.get("oneOf")
    if (enum is None) == (one_of is None):
        problems.add(path, "A choice lists its options in `enum` or in `oneOf`.")
        return None
    if enum is not None:
        if not isinstance(enum, list):
            problems.add(f"{path}.enum", "Must be a list of strings.")
            return None
        options = [Option(value) if isinstance(value, str) else None for value in enum]
    else:
        options = _described(one_of, f"{path}.oneOf", problems)
        if options is None:
            return None
    return _checked_choices(options, path, problems)


def _described(
    one_of: object, path: str, problems: _Problems
) -> list[Option | None] | None:
    if not isinstance(one_of, list):
        problems.add(path, "Must be a list of {const, description} objects.")
        return None
    options: list[Option | None] = []
    for index, entry in enumerate(one_of):
        if not isinstance(entry, Mapping) or "const" not in entry:
            problems.add(f"{path}[{index}]", "Must be an object with `const`.")
            options.append(None)
            continue
        problems.unknown_keywords(f"{path}[{index}]", entry, _LEVEL_KEYWORDS)
        described = entry.get("description")
        options.append(
            Option(entry["const"], described if isinstance(described, str) else None)
            if isinstance(entry["const"], (str, int))
            and not isinstance(entry["const"], bool)
            else None
        )
    return options


def _checked_choices(
    options: Sequence[Option | None], path: str, problems: _Problems
) -> tuple[Option, ...] | None:
    valid = [
        option
        for option in options
        if option is not None
        and isinstance(option.value, str)
        and 0 < len(option.value) <= MAX_OPTION_CHARS
    ]
    if len(valid) != len(options):
        problems.add(
            path, f"Every option is a non-empty string of at most {MAX_OPTION_CHARS}."
        )
        return None
    return _bounded(valid, path, problems, minimum=2, maximum=MAX_OPTIONS)


def _multi_choices(
    node: Mapping[str, object], path: str, problems: _Problems
) -> tuple[Option, ...] | None:
    if node.get("uniqueItems") is not True:
        problems.add(f"{path}.uniqueItems", "Must be true.")
    items = node.get("items")
    if not isinstance(items, Mapping) or items.get("type") != "string":
        problems.add(f"{path}.items", 'Must be a choice: {"type": "string", ...}.')
        return None
    problems.unknown_keywords(f"{path}.items", items, _CHOICE_KEYWORDS)
    return _choices(items, f"{path}.items", problems)


def _levels(
    node: Mapping[str, object], path: str, problems: _Problems
) -> tuple[Option, ...] | None:
    """A scale's levels: a `minimum`..`maximum` range, or described `oneOf` levels."""
    if "oneOf" in node:
        if "minimum" in node or "maximum" in node:
            problems.add(path, "A scale uses `oneOf` levels or a range, not both.")
            return None
        described = _described(node["oneOf"], f"{path}.oneOf", problems)
        if described is None:
            return None
        if any(option is None or isinstance(option.value, str) for option in described):
            problems.add(f"{path}.oneOf", "Every level's `const` is an integer.")
            return None
        levels = sorted(
            (option for option in described if option is not None),
            key=lambda option: int(option.value),
        )
        return _bounded(levels, path, problems, minimum=2, maximum=MAX_SCALE_LEVELS)
    low, high = node.get("minimum"), node.get("maximum")
    if not _is_int(low) or not _is_int(high):
        problems.add(path, "A scale needs integer `minimum` and `maximum`.")
        return None
    if not 2 <= high - low + 1 <= MAX_SCALE_LEVELS:
        problems.add(path, f"A scale has 2 to {MAX_SCALE_LEVELS} levels.")
        return None
    return tuple(Option(level) for level in range(low, high + 1))


def _bounded(
    options: Sequence[Option],
    path: str,
    problems: _Problems,
    *,
    minimum: int,
    maximum: int,
) -> tuple[Option, ...] | None:
    values = [option.value for option in options]
    if len(set(values)) != len(values):
        problems.add(path, "Options must be distinct.")
        return None
    if not minimum <= len(options) <= maximum:
        problems.add(path, f"Must have {minimum} to {maximum} options.")
        return None
    for option in options:
        if option.description and len(option.description) > MAX_DESCRIPTION_CHARS:
            problems.add(path, f"Descriptions are at most {MAX_DESCRIPTION_CHARS}.")
            return None
    return tuple(options)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)

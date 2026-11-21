"""A decision as asked, and as handed to a provider once it has been checked.

The instruction is the asker's and is trusted. The evidence and the examples are
whatever the asker read -- an email, a webhook body, a row -- and are not:
providers put them between markers and never follow them.

Nothing is truncated. A caller that has a 200 KB event decides what part of it
matters; quietly cutting it here would change the answer without saying so.

Images are evidence too: the bytes themselves, already read and sized by
whoever had the right to read them (the route reads a Lemma's files as the
caller). This module only bounds them, so a backend caller handing bytes in
directly is held to the same limits.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from app.modules.decisions.domain.answers import (
    AnswerValue,
    answer_problems,
    to_json,
)
from app.modules.decisions.domain.errors import DecisionInvalidError
from app.modules.decisions.domain.questions import DecisionSchema, parse_schema

DecisionPriority = Literal["interactive", "background"]

MAX_INSTRUCTION_CHARS = 8000
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_EXAMPLES = 20
MAX_EXAMPLE_BYTES = 32 * 1024
#: Few enough that every image's tokens fit the per-request ceiling, which a
#: model provider budgets per image.
MAX_IMAGES = 4
#: Per image, as it is sent. Under the smallest per-image limit of the model
#: providers a deployment runs (Anthropic's 5 MB, on the base64 text, which is
#: a third larger than this).
MAX_IMAGE_BYTES = 3 * 1024 * 1024
#: What every model provider behind the `model` provider takes.
IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


@dataclass(frozen=True, slots=True)
class DecisionExample:
    """A past case and how it was answered, to steer the provider.

    `answers` may leave questions out, and may answer one `None` when the right
    answer there was "can't tell".
    """

    evidence: JsonValue
    answers: Mapping[str, AnswerValue | None]


@dataclass(frozen=True, slots=True)
class DecisionImage:
    """One image to judge, as the provider will be sent it."""

    data: bytes
    media_type: str
    #: The file's name, so the instruction and the answer can tell images apart.
    #: Untrusted, like the rest of the evidence.
    name: str = ""


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    instruction: str
    #: Text or JSON. May be empty only when there are images to judge.
    evidence: JsonValue
    #: The questions, as a closed JSON Schema object (see `questions.py`).
    schema: Mapping[str, object]
    examples: Sequence[DecisionExample] = ()
    #: `interactive` for someone waiting on the answer (a call being routed),
    #: `background` for everything else. It sets how long the provider gets.
    priority: DecisionPriority = "background"
    #: Part of the evidence, after any text of it.
    images: Sequence[DecisionImage] = ()


@dataclass(frozen=True, slots=True)
class DecisionCaller:
    """Who is asking, for metering, rate limiting and the provider's own scoping."""

    user_id: UUID
    organization_id: UUID | None
    pod_id: UUID | None
    agent_id: UUID | None = None
    workload_type: str | None = None
    workload_id: UUID | None = None
    source_type: str = "decision"
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class RenderedExample:
    evidence: str
    answers: Mapping[str, AnswerValue | None]


@dataclass(frozen=True, slots=True)
class DecisionTask:
    """A checked request: what a provider is given."""

    instruction: str
    schema: DecisionSchema
    #: The evidence as the asker sent it, for a provider that takes structure.
    evidence: JsonValue
    #: The same evidence as text, for a provider that takes a prompt. Empty
    #: when the evidence is images alone.
    evidence_text: str
    examples: tuple[RenderedExample, ...] = ()
    priority: DecisionPriority = "background"
    images: tuple[DecisionImage, ...] = ()


class _Checks:
    def __init__(self) -> None:
        self.problems: list[dict[str, str]] = []
        self.too_large = False

    def add(self, path: str, message: str, *, too_large: bool = False) -> None:
        self.problems.append({"path": path, "message": message})
        self.too_large = self.too_large or too_large

    def error(self) -> DecisionInvalidError:
        return DecisionInvalidError(
            self.problems,
            code=(
                "DECISION_INPUT_TOO_LARGE"
                if self.too_large
                else "DECISION_INVALID_REQUEST"
            ),
        )


def build_task(request: DecisionRequest) -> DecisionTask:
    """Check everything about `request` at once, or raise one 422 listing it all."""
    checks = _Checks()
    schema, schema_problems = parse_schema(request.schema)
    for problem in schema_problems:
        checks.add(problem["path"], problem["message"])
    instruction = request.instruction.strip()
    if not instruction:
        checks.add("instruction", "Must not be empty.")
    elif len(instruction) > MAX_INSTRUCTION_CHARS:
        checks.add("instruction", f"At most {MAX_INSTRUCTION_CHARS} characters.")
    images = _images(request.images, checks)
    evidence_text = (
        ""
        if images and _empty(request.evidence)
        else _render(request.evidence, "evidence", MAX_EVIDENCE_BYTES, checks)
    )
    examples = _examples(schema, request.examples, checks) if schema is not None else ()
    if schema is None or evidence_text is None or checks.problems:
        raise checks.error()
    return DecisionTask(
        instruction=instruction,
        schema=schema,
        evidence=request.evidence,
        evidence_text=evidence_text,
        examples=examples,
        priority=request.priority,
        images=images,
    )


def _images(
    images: Sequence[DecisionImage], checks: _Checks
) -> tuple[DecisionImage, ...]:
    if len(images) > MAX_IMAGES:
        checks.add("images", f"At most {MAX_IMAGES} images.", too_large=True)
        return ()
    for index, image in enumerate(images):
        if image.media_type not in IMAGE_MEDIA_TYPES:
            checks.add(f"images[{index}]", "Must be a PNG, JPEG, WebP or GIF image.")
        elif not image.data:
            checks.add(f"images[{index}]", "Must not be empty.")
        elif len(image.data) > MAX_IMAGE_BYTES:
            checks.add(
                f"images[{index}]",
                f"At most {MAX_IMAGE_BYTES} bytes as sent.",
                too_large=True,
            )
    return tuple(images)


def _empty(evidence: JsonValue) -> bool:
    return evidence is None or evidence == ""


def _examples(
    schema: DecisionSchema, examples: Sequence[DecisionExample], checks: _Checks
) -> tuple[RenderedExample, ...]:
    if len(examples) > MAX_EXAMPLES:
        checks.add("examples", f"At most {MAX_EXAMPLES} examples.", too_large=True)
        return ()
    rendered: list[RenderedExample] = []
    for index, example in enumerate(examples):
        path = f"examples[{index}]"
        text = _render(example.evidence, f"{path}.evidence", MAX_EXAMPLE_BYTES, checks)
        answers = {key: to_json(value) for key, value in example.answers.items()}
        for problem in answer_problems(schema, answers, partial=True):
            checks.add(f"{path}.answers", problem)
        if text is not None:
            rendered.append(RenderedExample(evidence=text, answers=example.answers))
    if sum(len(example.evidence.encode()) for example in rendered) > MAX_EXAMPLE_BYTES:
        checks.add(
            "examples",
            f"Examples' evidence totals at most {MAX_EXAMPLE_BYTES} bytes.",
            too_large=True,
        )
    return tuple(rendered)


def _render(evidence: JsonValue, path: str, limit: int, checks: _Checks) -> str | None:
    if _empty(evidence):
        checks.add(path, "Must not be empty.")
        return None
    text = (
        evidence
        if isinstance(evidence, str)
        else json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )
    if len(text.encode()) > limit:
        checks.add(
            path, f"At most {limit} bytes. Send the part that matters.", too_large=True
        )
        return None
    return text

"""Request and response bodies for `POST /pods/{pod_id}/decisions`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
)

from app.modules.decisions.domain.answers import AnswerValue, DecisionResult, to_json
from app.modules.decisions.domain.request import (
    MAX_EXAMPLES,
    MAX_IMAGES,
    MAX_INSTRUCTION_CHARS,
    DecisionExample,
    DecisionImage,
    DecisionRequest,
)

#: Strict, so `true` is never read as `1` or `"1"` as a choice.
AnswerValueBody = StrictBool | StrictInt | StrictStr | list[StrictStr]


class DecisionExampleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: JsonValue = Field(description="A past case, as evidence was sent.")
    answers: dict[str, AnswerValueBody | None] = Field(
        description=(
            "How that case was answered, by question key. Questions may be left "
            "out; null means the right answer there was 'can't tell'."
        )
    )


class DecisionImageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "A file of this Lemma, by path (`/receipts/0042.jpg`, or `/me/...` "
            "for one of your own). Read with your access: a file you cannot "
            "read is refused (422)."
        ),
    )


class MakeDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    instruction: str = Field(
        min_length=1,
        max_length=MAX_INSTRUCTION_CHARS,
        description=(
            "What to judge and how, in your words. Trusted: it is the only part "
            "of the request the provider follows."
        ),
    )
    evidence: JsonValue = Field(
        default=None,
        description=(
            "What to judge: an email, an event, a row, as text or JSON. Never "
            "followed as instructions. At most 64 KiB; send the part that "
            "matters. Required unless `images` are sent."
        ),
    )
    images: list[DecisionImageBody] = Field(
        default_factory=list,
        max_length=MAX_IMAGES,
        description=(
            "Images to judge with the evidence -- a receipt photo, a slide -- as "
            "files of this Lemma: PNG, JPEG, WebP or GIF (still), at most 10 MiB "
            "each; larger than 1568 pixels a side is shrunk before it is sent. "
            "What an image says is evidence, never instructions. 422 "
            "`DECISION_IMAGES_UNSUPPORTED` when this server's decision model "
            "cannot read images."
        ),
    )
    output_schema: dict[str, JsonValue] = Field(
        alias="schema",
        description=(
            "The questions, as a flat JSON Schema object: one property per "
            "question, its `description` the question. Each property is a choice "
            '(`{"type": "string", "enum": [...]}`, or `oneOf` of `{const, '
            'description}`), a multi-choice (`{"type": "array", "items": '
            '<choice>, "uniqueItems": true}`), yes or no (`{"type": '
            '"boolean"}`), or a scale (`{"type": "integer", "minimum": 1, '
            '"maximum": 5}`, or `oneOf` of described integer levels). Free text '
            "and open-ended numbers are not supported."
        ),
    )
    examples: list[DecisionExampleBody] = Field(
        default_factory=list,
        max_length=MAX_EXAMPLES,
        description="Past cases and their answers, to steer the provider.",
    )
    priority: Literal["interactive", "background"] = Field(
        default="background",
        description=(
            "`interactive` when someone is waiting on the answer: a shorter "
            "deadline and no second attempt. `background` otherwise."
        ),
    )

    def to_request(self, images: Sequence[DecisionImage] = ()) -> DecisionRequest:
        """The request to ask, with `images` -- this body's `images`, as read."""
        return DecisionRequest(
            instruction=self.instruction,
            evidence=self.evidence,
            schema=self.output_schema,
            examples=tuple(
                DecisionExample(
                    evidence=example.evidence,
                    answers={
                        key: _answer_value(value)
                        for key, value in example.answers.items()
                    },
                )
                for example in self.examples
            ),
            priority=self.priority,
            images=tuple(images),
        )


class DecisionAnswerResponse(BaseModel):
    value: AnswerValueBody | None = Field(
        description=(
            "The answer, or null when the provider judged the evidence did not "
            "support one (the model provider does; Typesafe always answers the "
            "value it judged most likely). Treat null, or a confidence below "
            "your own threshold, as unsure. Null is an answer, not a failure: a "
            "failure is an error response."
        )
    )
    confidence: float | None = Field(
        description=(
            "The provider's probability for this value, when it measures one; "
            "for several choices, that of the least certain per-option "
            "decision, not of the whole selection. Null from providers that do "
            "not, such as a language model."
        )
    )


class DecisionUsageResponse(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None


class DecisionResponse(BaseModel):
    answers: dict[str, DecisionAnswerResponse] = Field(
        description="One answer per question, by key."
    )
    provider: str = Field(description="The provider that answered.")
    model: str | None = Field(description="The model that ran.")
    usage: DecisionUsageResponse

    @classmethod
    def from_result(cls, result: DecisionResult) -> DecisionResponse:
        return cls(
            answers={
                key: DecisionAnswerResponse.model_validate(
                    {"value": to_json(answer.value), "confidence": answer.confidence}
                )
                for key, answer in result.answers.items()
            },
            provider=result.provider,
            model=result.model,
            usage=DecisionUsageResponse(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            ),
        )


def _answer_value(value: bool | int | str | list[str] | None) -> AnswerValue | None:
    return tuple(value) if isinstance(value, list) else value

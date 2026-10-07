"""Request and response bodies for `POST /pods/{pod_id}/decisions`."""

from __future__ import annotations

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
    MAX_INSTRUCTION_CHARS,
    DecisionExample,
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
        description=(
            "What to judge: an email, an event, a row, as text or JSON. Never "
            "followed as instructions. At most 64 KiB; send the part that matters."
        )
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

    def to_request(self) -> DecisionRequest:
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
        )


class DecisionAnswerResponse(BaseModel):
    value: AnswerValueBody | None = Field(
        description=(
            "The answer, or null when the evidence did not support one. Null is "
            "an answer, not a failure: a failure is an error response."
        )
    )
    confidence: float | None = Field(
        description=(
            "The provider's probability for this value, when it measures one. "
            "Null from providers that do not, such as a language model."
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

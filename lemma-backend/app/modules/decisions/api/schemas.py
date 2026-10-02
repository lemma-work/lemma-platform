"""Request and response bodies for decisions and deciders."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.modules.decisions.domain.deciders import DeciderDefinition
from app.modules.decisions.domain.decisions import Answer, RungTrace
from app.modules.decisions.domain.questions import AnswerValue, Option

MAX_TEST_ROWS = 200


def _shorthand_options(value: object) -> object:
    """Accept `key: description` for options passed with a call, as definitions do."""
    if not isinstance(value, dict):
        return value
    normalized: dict[object, object] = {}
    for question, options in value.items():
        if isinstance(options, dict):
            normalized[question] = {
                key: {"description": option} if isinstance(option, str) else option
                for key, option in options.items()
            }
        else:
            normalized[question] = options
    return normalized


class _AskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decider: str | None = Field(
        default=None,
        description=(
            "A pod decider's name, or `system:<name>` for one that ships with "
            "Lemma. Leave out to ask the `definition` inline."
        ),
    )
    definition: DeciderDefinition | None = Field(
        default=None,
        description="Questions to ask without a named decider. Nothing is learned for them.",
    )
    options: dict[str, dict[str, Option]] = Field(
        default_factory=dict,
        description=(
            "Per question, options to add to the declared ones for this call: "
            "the person's pods, the open conversations, a list built just now."
        ),
    )
    record: bool = Field(
        default=True,
        description="Keep the decision. Off answers without keeping anything.",
    )
    visibility: Literal["PERSONAL", "POD"] = Field(
        default="PERSONAL",
        description=(
            "Who may read the recorded decision, evidence included: only you, or "
            "everyone in the pod who can read deciders. Personal unless the "
            "state is the pod's to share, because evidence is whatever was "
            "decided about -- an inbox, a call, a conversation."
        ),
    )

    @field_validator("options", mode="before")
    @classmethod
    def _shorthand(cls, value: object) -> object:
        return _shorthand_options(value)


class DecideBody(_AskBody):
    state: JsonValue = Field(
        description=(
            "The evidence: an event, a row, a message. Only the decider's input "
            "view of it reaches an engine."
        )
    )
    subject: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "What the decision is about, stable across retries. A decider is "
            "asked about a subject once; asking again returns the recorded "
            "decision."
        ),
    )


class DecideRowsBody(_AskBody):
    rows: list[JsonValue] = Field(min_length=1, max_length=5000)
    id_field: str | None = Field(
        default=None,
        description="The field that identifies a row. Row numbers are used when absent.",
    )
    subject_prefix: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Prefix for each row's subject (`<prefix>:<row id>`), so deciding "
            "the same rows again returns the recorded decisions."
        ),
    )


class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    decider_scope: str
    decider_name: str | None
    decider_version: int | None
    subject_key: str | None
    visibility: str
    answers: dict[str, Answer]
    open: list[str]
    status: str
    trace: list[RungTrace]
    evidence: str | None
    created_at: datetime
    answered_at: datetime | None
    answered_by_user_id: UUID | None


class DecisionListResponse(BaseModel):
    items: list[DecisionResponse]
    next_before: datetime | None = Field(
        default=None, description="Pass as `before` for the next, older page."
    )


class RowResultResponse(BaseModel):
    index: int
    row_id: str | None
    answers: dict[str, Answer]
    open: list[str]
    decision_id: UUID | None
    failed: bool


class RowsResponse(BaseModel):
    decider_key: str
    rows: list[RowResultResponse]
    counts: dict[str, dict[str, int]] = Field(
        description="Per question, how many rows got each answer."
    )
    failed: int


class AnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, AnswerValue] = Field(min_length=1)
    by: Literal["person", "agent"] = Field(
        default="person",
        description=(
            "Who answered. A person's answer teaches the decider; an agent's is "
            "recorded and never becomes an example."
        ),
    )


class CreateDeciderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    definition: DeciderDefinition


class UpdateDeciderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: DeciderDefinition


class DeciderResponse(BaseModel):
    id: UUID
    name: str
    version: int
    visibility: str
    definition: DeciderDefinition
    user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = Field(
        default_factory=list,
        description="Things the definition allows but that are usually a mistake.",
    )


class DeciderListResponse(BaseModel):
    items: list[DeciderResponse]


class DeciderVersionResponse(BaseModel):
    version: int
    definition: DeciderDefinition
    created_by: UUID | None
    created_at: datetime


class DeciderVersionListResponse(BaseModel):
    items: list[DeciderVersionResponse]


class SampleRowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: JsonValue
    expected: dict[str, AnswerValue] | None = None


class DeciderTestBody(_AskBody):
    rows: list[SampleRowBody] = Field(min_length=1, max_length=MAX_TEST_ROWS)


class Agreement(BaseModel):
    agreed: int
    total: int


class DisagreementResponse(BaseModel):
    row: int
    question: str
    expected: AnswerValue
    answer: Answer | None


class DeciderTestResponse(BaseModel):
    answers: list[dict[str, Answer]]
    open: list[list[str]]
    agreement: dict[str, Agreement]
    disagreements: list[DisagreementResponse]

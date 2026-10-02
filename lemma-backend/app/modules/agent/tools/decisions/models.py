"""Request models for the decisions toolset.

Every free-form value here -- a state, a definition, per-question options, the
answers to a decision -- takes a JSON object or a JSON-encoded string of one,
for the reason `PodWriteRecordRequest.data` does. A dynamic-key object
serializes with `properties: {}`, which many models read as "no fields" and
fill with `{}`; the string form is the unambiguous escape hatch. The validators
below decode it, so the tools only ever see objects.

The descriptions are the model-facing documentation of each argument, so they
say what to put there, not how it is implemented. Fields are declared in the
order a model should fill them: what to ask, then what to ask it about.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, ValidationInfo, field_validator

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.pod.models import RecordFilter, RecordSort

#: Rows an agent may write out inline. Past a few dozen the call is mostly
#: arguments, and a file or a table carries them without the model retyping
#: every row.
MAX_INLINE_ITEMS = 50


class InputRefused(ValueError):
    """A call that cannot go ahead as given. The message says what to pass instead.

    The problem is always the input -- too many rows, a file that is not CSV,
    two sources at once -- so it goes back to the model as the tool's error:
    it is the agent's to fix, not an operator's.
    """


_DEFINITION_SHAPE = (
    "A decider definition, as an object or its JSON string. Only `description` "
    "and `questions` are required:\n"
    '{"description": "What Kit does with each new support email.",\n'
    ' "guidance": "Kit files vendor invoices and answers customers about '
    'orders. Pricing exceptions and anything legal go to a person.",\n'
    ' "input": {"fields": ["from", "subject", "snippet"], "max_chars": 4000},\n'
    ' "questions": {"action": {"type": "choice", "prompt": "What should Kit do '
    'with this email?",\n'
    '   "options": {"act": "A customer is waiting, or an invoice needs filing.",\n'
    '     "digest": {"description": "Worth knowing, not urgent.", "not_for": '
    '"Anything a customer is waiting on."},\n'
    '     "ignore": "Newsletters, promotions, automated notices."},\n'
    '   "fallback": "digest"}},\n'
    ' "rules": [{"when": "contains(labels, \'CATEGORY_PROMOTIONS\')", '
    '"answer": {"action": "ignore"}}]}\n'
    "Question types: `choice` (one option; `fallback` is the option taken when "
    "nothing clearly fits), `multi_choice` (any of the options), `yes_no` "
    "(optional `yes`/`no` meanings), `scale` (2-10 `levels`, lowest first, "
    "answered with the level's index). Option and question keys are lowercase. "
    "An option is `key: description`, or an object with `description`, "
    "`not_for` and `examples`. `input.fields` are the only fields of a row an "
    "engine sees; list just what the questions need. `rules` answer before any "
    "engine: `when` is a JMESPath expression over those fields, or `phrases` "
    "are exact matches against the text at `field`."
)


def _decoded_object(value: object, *, argument: str) -> object:
    """`value`, with a JSON-encoded object string decoded; a blank string is None."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError(
            f"`{argument}` was a string but not valid JSON; pass an object, or a "
            "JSON-encoded string of one."
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"`{argument}` must be (or JSON-decode to) an object, not "
            f"{type(parsed).__name__}."
        )
    return parsed


def _decoded_state(value: object) -> object:
    """A state's JSON-encoded object decoded; any other string is kept as text."""
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    return parsed if isinstance(parsed, dict) else value


def _decoded_items(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ValueError(
            "`items` was a string but not valid JSON; pass a list of rows, or a "
            "JSON-encoded string of one."
        ) from exc
    if not isinstance(parsed, list):
        raise ValueError("`items` must be (or JSON-decode to) a list of rows.")
    return parsed


class TableRows(BaseModel):
    """Rows of a pod table, read with your own access: row-level security applies."""

    table_name: str
    filters: list[RecordFilter] = Field(
        default_factory=list, description="Only rows matching every filter."
    )
    sorts: list[RecordSort] = Field(default_factory=list)
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Decide at most this many matching rows, starting at `offset`. With "
            "`offset`, this is how a large table is worked through in batches. "
            "Leave it out to decide every matching row, which is refused when "
            "there are more than one call may decide."
        ),
    )
    offset: int = Field(default=0, ge=0)


# The row sources `decide` and `test_decider` share, declared once.
InlineItems = Annotated[
    list[JsonObject | str] | str | None,
    BeforeValidator(_decoded_items),
    Field(
        description=(
            f"Rows given inline, objects or plain strings; at most "
            f"{MAX_INLINE_ITEMS}. For more, write them to a CSV or JSONL file "
            "and pass `file`, or decide a `table`."
        )
    ),
]
RowsFile = Annotated[
    str | None,
    Field(
        description=(
            "A pod file of rows: CSV with a header row, or JSONL with one JSON "
            "object per line, e.g. `/me/leads.csv`. A relative path resolves "
            "against your pod working directory."
        )
    ),
]
RowsTable = Annotated[TableRows | None, Field(description="A pod table's rows.")]
RowKey = Annotated[
    str | None,
    Field(
        description=(
            "The field that identifies a row in the results. Defaults to a "
            "table's primary key, and to the row's number otherwise."
        )
    ),
]


class DecideRequest(BaseModel):
    decider: str | None = Field(
        default=None,
        description=(
            "A saved pod decider's name, or `system:<name>` for one that ships "
            "with Lemma. Leave it out to pass `questions` instead."
        ),
    )
    questions: JsonObject | str | None = Field(
        default=None,
        description=(
            "A one-off judgement: question key -> question, e.g. "
            '{"urgency": {"type": "scale", "prompt": "How urgent is this '
            'ticket?", "levels": ["low", "normal", "high"]}} or {"qualified": '
            '{"type": "choice", "prompt": "Is this lead a fit?", "options": '
            '{"yes": "Budget and need are clear.", "no": "Clearly not a fit.", '
            '"unsure": "Not enough to tell."}, "fallback": "unsure"}}. Types: '
            "`choice`, `multi_choice`, `yes_no`, `scale` (answered with the "
            "level's index, 0 = lowest)."
        ),
    )
    guidance: str | None = Field(
        default=None,
        description=(
            "With `questions`: a sentence or two on what the answers are for "
            "and how to judge."
        ),
    )
    definition: JsonObject | str | None = Field(
        default=None,
        description=(
            "A full definition that is not saved, in the shape `define_decider` "
            "takes: for running a draft you checked with `test_decider`."
        ),
    )
    options: JsonObject | str | None = Field(
        default=None,
        description=(
            "Per question, options to add for this call only, as `key: "
            'description`, e.g. {"pod": {"sales": "The sales pod", "ops": "The '
            'operations pod"}}: choices known only now. A declared option cannot '
            "be redefined."
        ),
    )
    state: JsonObject | str | None = Field(
        default=None,
        description=(
            "One thing to judge: an object (a message, a record, an event) or "
            "its JSON string. Any other string is judged as plain text. Only "
            "the decider's input fields of it reach an engine."
        ),
    )
    subject: str | None = Field(
        default=None,
        max_length=512,
        description=(
            "With `state`: a stable id for what it is about, e.g. "
            "`ticket:4812`. A decider is asked about a subject once; asking "
            "again returns the recorded decision instead of a new one."
        ),
    )
    items: InlineItems = None
    file: RowsFile = None
    table: RowsTable = None
    key: RowKey = None

    @field_validator("questions", "definition", "options", mode="before")
    @classmethod
    def _decode_objects(cls, value: object, info: ValidationInfo) -> object:
        return _decoded_object(value, argument=info.field_name or "value")

    @field_validator("state", mode="before")
    @classmethod
    def _decode_state(cls, value: object) -> object:
        return _decoded_state(value)


class DefineDeciderRequest(BaseModel):
    name: str = Field(
        description=(
            "Lowercase letters, digits, '-' or '_', e.g. `email-triage`. An "
            "existing name saves a new version; decisions already made keep the "
            "version that made them."
        )
    )
    definition: JsonObject | str = Field(description=_DEFINITION_SHAPE)

    @field_validator("definition", mode="before")
    @classmethod
    def _decode_definition(cls, value: object) -> object:
        return _decoded_object(value, argument="definition")


class DeciderTestRequest(BaseModel):
    decider: str | None = Field(
        default=None,
        description=(
            "A saved decider to try: a pod decider's name or `system:<name>`. "
            "Leave it out to pass a draft `definition`."
        ),
    )
    definition: JsonObject | str | None = Field(
        default=None,
        description=(
            "A draft definition to try before saving it, in the shape "
            "`define_decider` takes."
        ),
    )
    items: InlineItems = None
    file: RowsFile = None
    table: RowsTable = None
    key: RowKey = None
    expected: JsonObject | str | None = Field(
        default=None,
        description=(
            "Question key -> the field in each row that holds the answer a "
            'person gave, e.g. {"action": "label"}. Those fields are hidden from '
            "the decider, and its answers are compared with them. A row whose "
            "field is empty has no expectation."
        ),
    )

    @field_validator("definition", "expected", mode="before")
    @classmethod
    def _decode_objects(cls, value: object, info: ValidationInfo) -> object:
        return _decoded_object(value, argument=info.field_name or "value")


class AnswerDecisionRequest(BaseModel):
    decision_id: str = Field(description="The `decision_id` that `decide` returned.")
    answers: JsonObject | str = Field(
        description=(
            "Question key -> answer: an option key for `choice`, a list of "
            "option keys for `multi_choice`, true or false for `yes_no`, the "
            "level's index for `scale` (0 = lowest)."
        )
    )

    @field_validator("answers", mode="before")
    @classmethod
    def _decode_answers(cls, value: object) -> object:
        return _decoded_object(value, argument="answers")

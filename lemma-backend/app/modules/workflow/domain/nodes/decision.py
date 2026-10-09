"""Decision node configuration: branch on a rule, or on a judgement.

A decision node chooses the next node one of two ways. `rules` are JMESPath
conditions over the run context, evaluated in place. `question` asks one closed
question about some evidence through the decisions module -- a choice, yes or
no, or a point on a short scale -- and routes on the answer. The two never mix
on one node: a node that both evaluated rules and asked a question would have
two answers to "why did the run go there".
"""

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

from app.modules.workflow.domain.nodes.base import BaseNode, NodeType
from app.modules.workflow.domain.nodes.bindings import InputBinding

#: The key the node's one question is asked under. The decisions contract asks
#: a set of questions by key; a node asks exactly one, so it names it once.
ANSWER_KEY = "answer"
MAX_QUESTION_INSTRUCTION_CHARS = 4000
MAX_QUESTION_EXAMPLES = 20

#: One answer to a single-value question: a choice, yes or no, or a level.
#: Strict, so `true` is never read as `1`, nor `"1"` as a level.
QuestionAnswer = StrictBool | StrictInt | StrictStr


class DecisionRule(BaseModel):
    condition: str = Field(
        ...,
        description=(
            "JMESPath condition evaluated against the run context. The first "
            "rule whose condition is truthy selects the next node. "
            "Example: `collect_input.decision == 'approved'`."
        ),
    )
    next_node_id: str


class DecisionQuestionExample(BaseModel):
    """A past case and how it should have been answered."""

    model_config = ConfigDict(extra="forbid")

    evidence: JsonValue = Field(description="The evidence of a past case.")
    answer: QuestionAnswer | None = Field(
        description=(
            "How that case was answered: one of the question's values, or null "
            "when the right answer there was 'can't tell'."
        )
    )


class DecisionQuestion(BaseModel):
    """One closed question, asked about one piece of evidence, routed on."""

    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(
        ...,
        min_length=1,
        max_length=MAX_QUESTION_INSTRUCTION_CHARS,
        description=(
            "What to judge and how, in the author's words. Trusted: it is the "
            "only part of the question the provider follows."
        ),
    )
    evidence: InputBinding = Field(
        ...,
        description=(
            "What to judge, as an input binding resolved against the run "
            "context -- an email, an event, a row. Never followed as "
            "instructions."
        ),
        json_schema_extra={
            "example": {"type": "expression", "value": "start.payload.email"}
        },
    )
    answer: dict[str, JsonValue] = Field(
        ...,
        description=(
            "The question, as one closed JSON Schema property whose "
            '`description` is the question: a choice (`{"type": "string", '
            '"enum": [...]}` or `oneOf` of `{const, description}`), yes or no '
            '(`{"type": "boolean"}`), or a scale (`{"type": "integer", '
            '"minimum": 1, "maximum": 5}` or `oneOf` of integer levels). A '
            "multi-choice is not routable and is refused."
        ),
        json_schema_extra={
            "example": {
                "type": "string",
                "enum": ["billing", "bug", "other"],
                "description": "What is this support email about?",
            }
        },
    )
    examples: list[DecisionQuestionExample] = Field(
        default_factory=list,
        max_length=MAX_QUESTION_EXAMPLES,
        description="Past cases and their answers, to steer the provider.",
    )
    routes: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Next node id by answer, the answer written as a string: `true` or "
            "`false` for yes or no, `3` for a scale level, the option itself "
            "for a choice. An answer with no route takes the node's default "
            "edge -- its first outgoing edge."
        ),
        json_schema_extra={"example": {"billing": "refund", "bug": "file_bug"}},
    )
    unsure_next_node_id: str | None = Field(
        default=None,
        description=(
            "Where to go when the evidence does not support an answer, or the "
            "answer's confidence is below `min_confidence`. Unset, an unsure "
            "answer takes the default edge."
        ),
    )
    min_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "An answer whose confidence is below this counts as unsure. Ignored "
            "when the provider reports no confidence, as a language model does."
        ),
    )

    def asked_schema(self) -> dict[str, JsonValue]:
        """The question as the decisions contract asks it: one property."""
        return {"type": "object", "properties": {ANSWER_KEY: self.answer}}

    def targets(self) -> list[str]:
        targets = list(self.routes.values())
        if self.unsure_next_node_id is not None:
            targets.append(self.unsure_next_node_id)
        return targets


class DecisionNodeConfig(BaseModel):
    """Configuration for Decision node: `rules`, or a `question`, never both."""

    rules: list[DecisionRule] = Field(
        default_factory=list,
        description=(
            "Conditions evaluated in order against the run context; the first "
            "truthy one picks the next node."
        ),
    )
    question: DecisionQuestion | None = Field(
        default=None,
        description=(
            "Ask one closed question about some evidence and route on the "
            "answer. The run waits while it is asked; an answer that cannot be "
            "had fails the run rather than taking any branch."
        ),
    )

    def targets(self) -> list[str]:
        """Every node this decision may send a run to, other than by an edge."""
        targets = [rule.next_node_id for rule in self.rules]
        if self.question is not None:
            targets.extend(self.question.targets())
        return targets


class DecisionNode(BaseNode):
    """Decision node. Routes to the first rule whose condition is truthy, or on
    the answer to its question; falls through to the default outgoing edge
    when nothing routes."""

    type: Literal[NodeType.DECISION] = NodeType.DECISION
    config: DecisionNodeConfig

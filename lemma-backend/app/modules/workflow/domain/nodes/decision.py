"""Decision node configuration."""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.modules.decisions.contracts.shapes import (
    ChoiceQuestion,
    DeciderDefinition,
)
from app.modules.workflow.domain.nodes.base import BaseNode, NodeType
from app.modules.workflow.domain.nodes.bindings import InputBinding

#: A pod decider's name, or `system:<name>` for one that ships with Lemma.
DECIDER_REF_PATTERN = r"^(system:)?[a-z0-9][a-z0-9_-]{0,63}$"
QUESTION_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


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


class DecisionNodeQuestion(BaseModel):
    """A closed question the node asks when none of its rules matched.

    Answered by the decisions module -- the decider's own rules, then System
    One, then the system model -- and recorded once per step, so a resumed or
    retried step reads the answer it already has.
    """

    input: InputBinding | dict[str, InputBinding] = Field(
        ...,
        description=(
            "What the decision is about: one binding, or named bindings that "
            "become an object. Only the decider's input view of it reaches an "
            "engine."
        ),
        examples=[
            {
                "from": {"type": "expression", "value": "start.payload.from"},
                "subject": {"type": "expression", "value": "start.payload.subject"},
            }
        ],
    )
    decider: str | None = Field(
        default=None,
        pattern=DECIDER_REF_PATTERN,
        description=(
            "A pod decider's name, or `system:<name>` for one that ships with "
            "Lemma. Leave out to ask `definition` inline."
        ),
        examples=["email-triage", "system:reply_to_question"],
    )
    definition: DeciderDefinition | None = Field(
        default=None,
        description=(
            "A decider asked inline: exactly one `choice` question. Nothing is "
            "learned for it; name a pod decider for answers people can correct."
        ),
    )
    question_key: str | None = Field(
        default=None,
        pattern=QUESTION_KEY_PATTERN,
        description="The question to branch on. Leave out when the decider asks one.",
    )
    branches: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Option key to the id of the node that answer goes to. An answer "
            "with no branch falls through to the default outgoing edge."
        ),
        examples=[{"act": "draft_reply", "ignore": "archive"}],
    )
    on_open: str | None = Field(
        default=None,
        description=(
            "The node to go to when no rung could answer. Without it an open "
            "question takes its fallback option's branch, or falls through."
        ),
    )

    @model_validator(mode="after")
    def _one_source(self) -> "DecisionNodeQuestion":
        if (self.decider is None) == (self.definition is None):
            raise ValueError(
                "a question names a `decider` or carries a `definition`, "
                "not both and not neither"
            )
        if self.definition is not None:
            self._check_inline(self.definition)
        return self

    def _check_inline(self, definition: DeciderDefinition) -> None:
        """An inline definition is checked here, because nothing else can.

        A named decider's questions are only known when the step runs; an
        inline one is all here, so a branch that names no option is a typo
        refused at save rather than an answer that silently falls through.
        """
        if len(definition.questions) != 1:
            raise ValueError("an inline definition asks exactly one question")
        [(key, question)] = definition.questions.items()
        if not isinstance(question, ChoiceQuestion) or len(question.options or {}) < 2:
            raise ValueError(
                "an inline definition's question is a `choice` with at least "
                "two declared options"
            )
        if self.question_key not in (None, key):
            raise ValueError(
                f"question_key {self.question_key!r} is not the definition's "
                f"question {key!r}"
            )
        unknown = sorted(set(self.branches) - set(question.options or {}))
        if unknown:
            raise ValueError(f"branches {unknown} are not options of {key!r}")

    def bindings(self) -> dict[str, InputBinding]:
        """The input as named bindings; a single binding is named `input`."""
        return self.input if isinstance(self.input, dict) else {"input": self.input}

    def targets(self) -> list[str]:
        return [*self.branches.values(), *([self.on_open] if self.on_open else [])]

    def route(self, choice: object, open_questions: Sequence[str]) -> str | None:
        """The node an answer goes to, or None to fall through to the edge.

        An open question goes to `on_open` when there is one. Otherwise the
        answer that came back -- for an open choice, its fallback option --
        picks a branch like any other.
        """
        is_open = (
            self.question_key in open_questions
            if self.question_key
            else bool(open_questions)
        )
        if is_open and self.on_open:
            return self.on_open
        return self.branches.get(choice) if isinstance(choice, str) else None


class DecisionNodeConfig(BaseModel):
    """Configuration for Decision node."""

    rules: list[DecisionRule] = Field(default_factory=list)
    question: DecisionNodeQuestion | None = Field(
        default=None,
        description=(
            "Asked only when no rule matched: a decider's closed question, "
            "with a branch per option. The node needs rules, a question, or "
            "both."
        ),
    )

    def targets(self) -> list[str]:
        """Every node this decision can hand control to other than by its edge."""
        asked = self.question.targets() if self.question else []
        return [*(rule.next_node_id for rule in self.rules), *asked]


class DecisionNode(BaseNode):
    """Decision node. Routes to the first rule whose condition is truthy,
    then asks its question when it has one; falls through to the default
    outgoing edge when neither picks a node."""

    type: Literal[NodeType.DECISION] = NodeType.DECISION
    config: DecisionNodeConfig

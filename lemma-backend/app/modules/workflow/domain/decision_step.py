"""What a DECISION node that asks a question hands on, and gets back.

Asking goes through the decisions module and can climb to System One or a
model: seconds of external I/O that the engine, which advances a run inside one
transaction with its row locked, must not wait on. So the node suspends on a
DECISION wait carrying a `DecisionAsk`; a job answers it with no session open,
and the run resumes with a `DecisionOutcome` as the node's output.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.modules.decisions.contracts.shapes import DeciderDefinition

#: The longest subject the decisions module files a decision under.
MAX_SUBJECT_LENGTH = 512
#: Longer node ids are hashed into the subject, so any id fits the limit above.
_MAX_NODE_ID_IN_SUBJECT = 200

#: An answer as the decisions module gives one: an option key, several of them,
#: yes or no, or the index of a scale's level.
AnswerValue = str | list[str] | bool | int


class DecisionAsk(BaseModel):
    """The question a suspended DECISION step waits on, as its wait row keeps it.

    The state is resolved when the step suspends, so the job asks about what
    the run held at that moment rather than re-reading a context that may have
    moved on.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(max_length=MAX_SUBJECT_LENGTH)
    state: JsonValue
    decider: str | None = None
    definition: DeciderDefinition | None = None
    question_key: str | None = None


class DecisionOutcome(BaseModel):
    """A question-asking DECISION node's output.

    Always the same keys, so a later step can bind `<node>.choice` whichever
    way the node went: a rule that matched leaves the rest empty; an asked
    question leaves `matched_condition` empty. `choice` is the answer to the
    question the node branches on, which for a question left open is its
    fallback option when it has one; `answered_by` is empty then, and `open`
    names every question the decision left open.
    """

    matched_condition: str | None = None
    choice: AnswerValue | None = None
    decision_id: UUID | None = None
    open: list[str] = Field(default_factory=list)
    answered_by: str | None = None

    def as_output(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")


def decision_subject(
    run_id: UUID,
    node_id: str,
    loop_path: Sequence[int],
    taken: Collection[str],
) -> str:
    """What the decision one step asks is filed under.

    `workflow:<run>:<node>:<loop index>`, so a retried or resumed step reads the
    decision it already recorded instead of asking again. Nested loops name
    each level's index, outermost first. A step that a cycle in the graph
    brings back to the same place gets `:2`, `:3`... after that: its state has
    moved on, and the recorded answer is about the visit before.
    """
    node_part = (
        node_id
        if len(node_id) <= _MAX_NODE_ID_IN_SUBJECT
        else "sha256-" + hashlib.sha256(node_id.encode()).hexdigest()
    )
    position = ".".join(str(index) for index in loop_path) or "0"
    base = f"workflow:{run_id}:{node_part}:{position}"
    if base not in taken:
        return base
    visit = 2
    while f"{base}:{visit}" in taken:
        visit += 1
    return f"{base}:{visit}"

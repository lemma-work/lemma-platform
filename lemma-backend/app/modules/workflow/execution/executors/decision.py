"""Decision node executor: the first truthy rule wins, then the question."""

from pydantic import JsonValue

from app.modules.workflow.domain.decision_step import (
    DecisionAsk,
    DecisionOutcome,
    decision_subject,
)
from app.modules.workflow.domain.nodes import DecisionNode, DecisionNodeQuestion
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.outcome import Branch, NodeOutcome, Suspend
from app.modules.workflow.execution.step_context import StepContext


class DecisionExecutor:
    async def execute(self, node: DecisionNode, step: StepContext) -> NodeOutcome:
        question = node.config.question
        for rule in node.config.rules:
            # Evaluation errors propagate and fail the run loudly; conditions
            # were compile-checked at save time, so a failure here is a
            # genuine runtime problem worth surfacing.
            if step.context.resolve_condition(rule.condition):
                return Branch(
                    next_node_id=rule.next_node_id,
                    output=_matched(rule.condition, question),
                )
        if question is None:
            # No rule matched: fall through to the default outgoing edge.
            return Branch(next_node_id=None, output={"matched_condition": None})
        # Asking can climb to System One or a model, and this runs inside the
        # engine's transaction with the run row locked. So the step suspends,
        # and a job asks once the wait row has committed.
        return Suspend(wait=_ask(node.id, question, step))


def _matched(
    condition: str, question: DecisionNodeQuestion | None
) -> dict[str, JsonValue]:
    """A matched rule's output: the full shape once the node can also ask."""
    if question is None:
        return {"matched_condition": condition}
    return DecisionOutcome(matched_condition=condition).as_output()


def _ask(
    node_id: str, question: DecisionNodeQuestion, step: StepContext
) -> WaitRequest:
    subject = decision_subject(step.run_id, node_id, step.loop_path, step.earlier_refs)
    resolved = step.context.resolve_inputs(question.bindings())
    ask = DecisionAsk(
        subject=subject,
        state=resolved if isinstance(question.input, dict) else resolved["input"],
        decider=question.decider,
        definition=question.definition,
        question_key=question.question_key,
    )
    return WaitRequest(
        wait_type=WorkflowRunWaitType.DECISION,
        external_ref=subject,
        payload=ask.model_dump(mode="json"),
    )

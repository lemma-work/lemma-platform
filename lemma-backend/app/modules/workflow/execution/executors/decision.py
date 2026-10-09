"""Decision node executor: first truthy rule wins, or ask the node's question.

A question is not answered here. The executor resolves the evidence, writes
everything the question needs onto a DECISION wait, and suspends -- the same
shape as a function node, so the engine never holds its run-row lock across a
model call. A job asks the question once the wait is committed and resumes the
run on the answer's route.
"""

from uuid import uuid4

from app.modules.workflow.domain.decision_questions import DecisionAsk
from app.modules.workflow.domain.nodes import DecisionNode, DecisionQuestion
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.outcome import Branch, NodeOutcome, Suspend
from app.modules.workflow.execution.step_context import StepContext

_EVIDENCE = "evidence"


class DecisionExecutor:
    async def execute(self, node: DecisionNode, step: StepContext) -> NodeOutcome:
        if node.config.question is not None:
            return self._ask(node.id, node.config.question, step)
        for rule in node.config.rules:
            # Evaluation errors propagate and fail the run loudly; conditions
            # were compile-checked at save time, so a failure here is a
            # genuine runtime problem worth surfacing.
            if step.context.resolve_condition(rule.condition):
                return Branch(
                    next_node_id=rule.next_node_id,
                    output={"matched_condition": rule.condition},
                )
        # No rule matched: fall through to the default outgoing edge.
        return Branch(next_node_id=None, output={"matched_condition": None})

    @staticmethod
    def _ask(node_id: str, question: DecisionQuestion, step: StepContext) -> Suspend:
        # Resolved like any node input: a required expression that finds
        # nothing fails the run here, naming the path, before anything is asked.
        evidence = step.context.resolve_inputs({_EVIDENCE: question.evidence})[
            _EVIDENCE
        ]
        external_ref = str(uuid4())
        step.decision.ask_once_committed(external_ref)
        return Suspend(
            wait=WaitRequest(
                wait_type=WorkflowRunWaitType.DECISION,
                external_ref=external_ref,
                payload=DecisionAsk.for_question(
                    node_id, question, evidence
                ).to_payload(),
            )
        )

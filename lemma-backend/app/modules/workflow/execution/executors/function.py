"""Function node executor: inline result or FUNCTION wait for async runs."""

from app.modules.workflow.domain.nodes import FunctionNode
from app.modules.workflow.domain.wait import WaitRequest, WorkflowRunWaitType
from app.modules.workflow.execution.outcome import Advance, NodeOutcome, Suspend
from app.modules.workflow.execution.step_context import StepContext


class FunctionExecutor:
    async def execute(self, node: FunctionNode, step: StepContext) -> NodeOutcome:
        inputs = step.context.resolve_inputs(node.config.input_mapping)

        result = await step.function.execute_function(
            node.config.function_name,
            inputs,
            step.pod_id,
            step.user_id,
            ctx=step.authz_ctx,
        )
        # A dispatched run (any function type) suspends the workflow on its run
        # id. The run and its `function.run` item were written on this run's
        # transaction; the run's terminal transition writes the
        # `workflow.resume` that wakes this wait. The engine never holds its
        # run-row lock across the sandbox round trip.
        if result.get("run_id") and result.get("status") in {"PENDING", "RUNNING"}:
            return Suspend(
                wait=WaitRequest(
                    wait_type=WorkflowRunWaitType.FUNCTION,
                    external_ref=str(result["run_id"]),
                    payload={"function_name": node.config.function_name},
                )
            )
        return Advance(output=result)

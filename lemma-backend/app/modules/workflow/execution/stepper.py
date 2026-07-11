"""The stepping loop: executes nodes until the run waits, completes, or fails.

The stepper is the only writer of run context and loop frames. Executors
return outcomes; the stepper interprets them and manages transitions.
"""

from dataclasses import dataclass

from app.core.authorization.context import Context
from app.modules.workflow.domain.context import LoopScope, RunContextReader
from app.modules.workflow.domain.errors import WorkflowDomainError
from app.modules.workflow.domain.workflow import WorkflowEntity
from app.modules.workflow.domain.ports import AgentPort, FunctionPort, SchedulePort
from app.modules.workflow.domain.run import WorkflowRunEntity, WorkflowRunStatus, LoopFrame
from app.modules.workflow.domain.wait import WaitRequest
from app.modules.workflow.domain.wait import WorkflowRunWaitType
from app.modules.workflow.execution.executors import EXECUTOR_REGISTRY, NodeExecutor
from app.modules.workflow.execution.outcome import (
    Advance,
    Branch,
    Halt,
    StartLoop,
    Suspend,
)
from app.modules.workflow.execution.step_context import StepContext
from app.modules.workflow.domain.nodes import NodeType
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Hard ceiling on node executions per advance — a cycle that never suspends
# fails loudly instead of spinning forever.
MAX_STEPS_PER_ADVANCE = 1000


@dataclass
class StepResult:
    """What the engine needs after an advance: the pending wait, if any."""

    wait: WaitRequest | None = None


class RunStepper:
    def __init__(
        self,
        *,
        agent: AgentPort,
        function: FunctionPort,
        schedule: SchedulePort,
        authz_ctx: Context | None = None,
        registry: dict[NodeType, NodeExecutor] | None = None,
    ):
        self._agent = agent
        self._function = function
        self._schedule = schedule
        self._authz_ctx = authz_ctx
        self._registry = registry or EXECUTOR_REGISTRY

    async def advance(self, run: WorkflowRunEntity, flow: WorkflowEntity) -> StepResult:
        """Execute from run.current_node_id until WAITING/COMPLETED/FAILED."""
        steps = 0
        while run.status == WorkflowRunStatus.RUNNING:
            if run.current_node_id is None:
                run.complete()
                break
            steps += 1
            if steps > MAX_STEPS_PER_ADVANCE:
                run.fail(
                    f"Run exceeded {MAX_STEPS_PER_ADVANCE} node executions in a "
                    "single advance — the graph likely contains a cycle that "
                    "never waits"
                )
                break

            try:
                node = flow.get_node(run.current_node_id)
            except ValueError:
                run.fail(f"Node {run.current_node_id} not found")
                break

            executor = self._registry[node.type]
            step = run.begin_step(node.id)
            logger.info(
                "workflow.step.started",
                run_id=str(run.id),
                flow_id=str(flow.id),
                node_id=node.id,
                node_type=node.type.value,
                step_index=step.step_index,
            )
            try:
                outcome = await executor.execute(node, self._step_context(run, flow))
            except WorkflowDomainError as exc:
                run.fail(exc.message, node_id=node.id)
                break
            except Exception as exc:  # noqa: BLE001 — every executor error fails the run
                run.fail(f"Node '{node.id}' execution failed: {exc}", node_id=node.id)
                break

            if isinstance(outcome, Suspend):
                run.suspend_step(
                    step,
                    outcome.output or None,
                    human_wait=outcome.wait.wait_type == WorkflowRunWaitType.HUMAN,
                )
                logger.info(
                    "workflow.step.suspended",
                    run_id=str(run.id),
                    node_id=node.id,
                    wait_type=outcome.wait.wait_type.value,
                )
                return StepResult(wait=outcome.wait)

            if isinstance(outcome, Halt):
                run.complete_step(step, outcome.output or None)
                if outcome.output:
                    run.record_node_output(node.id, outcome.output)
                run.complete()
                break

            if isinstance(outcome, StartLoop):
                run.complete_step(step, {"items_count": len(outcome.items)})
                if outcome.items:
                    frame = LoopFrame(
                        loop_node_id=node.id,
                        body_node_id=outcome.body_node_id,
                        index=0,
                        items=outcome.items,
                        item_var=outcome.item_var,
                    )
                    run.execution_stack.append(frame)
                    self._seed_loop_scope(run, frame)
                    run.current_node_id = outcome.body_node_id
                else:
                    run.record_node_output(node.id, {"results": [], "count": 0})
                    self.move_past(run, flow, node.id)
                continue

            if isinstance(outcome, Branch):
                run.complete_step(step, outcome.output or None)
                run.record_node_output(node.id, outcome.output)
                self.move_past(run, flow, node.id, forced=outcome.next_node_id)
                continue

            # Advance
            assert isinstance(outcome, Advance)
            run.complete_step(step, outcome.output or None)
            run.record_node_output(node.id, outcome.output)
            self.move_past(run, flow, node.id)

        self._log_outcome(run)
        return StepResult()

    async def continue_after(
        self, run: WorkflowRunEntity, flow: WorkflowEntity, node_id: str
    ) -> StepResult:
        """Continue execution after node_id completed externally (resume)."""
        self.move_past(run, flow, node_id)
        if run.status != WorkflowRunStatus.RUNNING:
            self._log_outcome(run)
            return StepResult()
        return await self.advance(run, flow)

    # -- transition helpers ----------------------------------------------------

    def move_past(
        self,
        run: WorkflowRunEntity,
        flow: WorkflowEntity,
        from_node_id: str,
        *,
        forced: str | None = None,
    ) -> None:
        """Position the run after from_node_id, handling chain ends and loop
        iteration bookkeeping. May complete or fail the run."""
        target = forced or flow.next_after(from_node_id)
        while True:
            frame = run.execution_stack[-1] if run.execution_stack else None
            if target is not None:
                if frame is not None and target == frame.loop_node_id:
                    # Explicit edge back to the active loop node means "end of
                    # iteration", not a fresh loop entry.
                    target = None
                    continue
                if not flow.has_node(target):
                    run.fail(f"Next node '{target}' not found in flow")
                    return
                run.current_node_id = target
                return

            # Chain end
            if frame is None:
                run.complete()
                return

            # An iteration of the innermost loop finished.
            frame.results.append(run.execution_context.node_output(frame.body_node_id))
            frame.index += 1
            if frame.index < len(frame.items):
                self._seed_loop_scope(run, frame)
                run.current_node_id = frame.body_node_id
                return

            # Loop exhausted: pop, record aggregate output, restore outer scope.
            run.execution_stack.pop()
            run.record_node_output(
                frame.loop_node_id,
                {"results": frame.results, "count": len(frame.items)},
            )
            outer = run.execution_stack[-1] if run.execution_stack else None
            if outer is not None:
                self._seed_loop_scope(run, outer)
            else:
                run.execution_context.set_loop_scope(None)
            target = flow.next_after(frame.loop_node_id)

    def _seed_loop_scope(self, run: WorkflowRunEntity, frame: LoopFrame) -> None:
        run.execution_context.set_loop_scope(
            LoopScope(
                item=frame.items[frame.index],
                index=frame.index,
                count=len(frame.items),
                item_var=frame.item_var,
            )
        )

    def _step_context(self, run: WorkflowRunEntity, flow: WorkflowEntity) -> StepContext:
        return StepContext(
            run_id=run.id,
            flow_id=flow.id,
            pod_id=run.pod_id,
            user_id=run.user_id,
            context=RunContextReader(run.execution_context),
            agent=self._agent,
            function=self._function,
            schedule=self._schedule,
            authz_ctx=self._authz_ctx,
        )

    def _log_outcome(self, run: WorkflowRunEntity) -> None:
        if run.status == WorkflowRunStatus.COMPLETED:
            logger.info("workflow.run.completed", run_id=str(run.id))
        elif run.status == WorkflowRunStatus.FAILED:
            logger.warning(
                "workflow.run.failed",
                run_id=str(run.id),
                failed_node_id=run.failed_node_id,
                error=run.error,
            )

"""Node executors, resolved by node type.

One executor per node type. Executors are stateless; per-step data arrives
via StepContext.
"""

from typing import Protocol

from app.modules.workflow.domain.nodes import NodeType, WorkflowNode
from app.modules.workflow.execution.outcome import NodeOutcome
from app.modules.workflow.execution.step_context import StepContext

from app.modules.workflow.execution.executors.form import FormExecutor
from app.modules.workflow.execution.executors.agent import AgentExecutor
from app.modules.workflow.execution.executors.function import FunctionExecutor
from app.modules.workflow.execution.executors.decision import DecisionExecutor
from app.modules.workflow.execution.executors.loop import LoopExecutor
from app.modules.workflow.execution.executors.wait_until import WaitUntilExecutor
from app.modules.workflow.execution.executors.notify import NotifyExecutor
from app.modules.workflow.execution.executors.end import EndExecutor


class NodeExecutor(Protocol):
    async def execute(self, node: WorkflowNode, step: StepContext) -> NodeOutcome: ...


EXECUTOR_REGISTRY: dict[NodeType, NodeExecutor] = {
    NodeType.FORM: FormExecutor(),
    NodeType.AGENT: AgentExecutor(),
    NodeType.FUNCTION: FunctionExecutor(),
    NodeType.DECISION: DecisionExecutor(),
    NodeType.LOOP: LoopExecutor(),
    NodeType.WAIT_UNTIL: WaitUntilExecutor(),
    NodeType.NOTIFY: NotifyExecutor(),
    NodeType.END: EndExecutor(),
}

__all__ = ["EXECUTOR_REGISTRY", "NodeExecutor"]

"""Typed execution context handed to node executors."""

from dataclasses import dataclass, field
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.workflow.domain.context import ContextReader
from app.modules.workflow.domain.ports import AgentPort, FunctionPort, SchedulePort


@dataclass
class StepContext:
    """Everything an executor may use: identifiers, a read-only context
    reader, and typed ports. Executors never mutate run state."""

    run_id: UUID
    flow_id: UUID
    pod_id: UUID
    user_id: UUID
    context: ContextReader
    agent: AgentPort
    function: FunctionPort
    schedule: SchedulePort
    authz_ctx: Context | None = None
    #: The index of each loop this step runs inside, outermost first.
    loop_path: tuple[int, ...] = ()
    #: What this node's earlier steps in the run suspended on, so a step that a
    #: cycle brings back to the same node can tell its visits apart.
    earlier_refs: frozenset[str] = field(default_factory=frozenset)

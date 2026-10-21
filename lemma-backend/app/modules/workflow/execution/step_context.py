"""Typed execution context handed to node executors."""

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.work import WorkWriter
from app.modules.workflow.domain.context import ContextReader
from app.modules.workflow.domain.ports import AgentPort, FunctionPort


@dataclass
class StepContext:
    """Everything an executor may use: identifiers, a read-only context
    reader, typed ports onto other modules' rows, and a writer for the work
    items its suspension needs -- all on the run's own transaction.

    Nothing here reaches the network. A step that needs the outside world
    (a model call, a sandbox) suspends and writes the work item that does it,
    which is what lets a run advance inside a FAST work handler. Executors
    never mutate run state."""

    run_id: UUID
    flow_id: UUID
    pod_id: UUID
    user_id: UUID
    context: ContextReader
    agent: AgentPort
    function: FunctionPort
    work: WorkWriter
    authz_ctx: Context | None = None

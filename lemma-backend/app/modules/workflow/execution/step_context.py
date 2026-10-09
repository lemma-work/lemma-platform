"""Typed execution context handed to node executors."""

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.workflow.domain.context import ContextReader
from app.modules.workflow.domain.ports import (
    AgentPort,
    DecisionPort,
    FunctionPort,
    SchedulePort,
)


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
    decision: DecisionPort
    authz_ctx: Context | None = None

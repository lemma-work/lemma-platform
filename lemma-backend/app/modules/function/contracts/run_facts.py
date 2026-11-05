"""What the function module announces when a run ends.

A run's terminal transition emits ``FunctionRunFinished`` in the transaction
that makes it terminal, so whatever waits on the run (a workflow step) gets its
work item committed with the outcome -- there is no stream, consumer group or
sweep between them. The fact carries ids; a subscriber reads the run's output
from the row.

A submodule rather than ``contracts/__init__``: that one imports the API
schemas, and a subscriber registering at wiring time should not pay for them.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.core.infrastructure.work import Fact, pod_namespace

FunctionRunOutcome = Literal["COMPLETED", "FAILED", "CANCELLED"]


class FunctionRunFinished(Fact):
    function_run_id: UUID
    pod_id: UUID
    function_id: UUID | None = None
    status: FunctionRunOutcome

    def work_namespace(self) -> str:
        return pod_namespace(self.pod_id)


__all__ = ["FunctionRunFinished", "FunctionRunOutcome"]

"""What a resume's caller already read, handed to ``WorkflowEngine.resume_internal``.

A step's transaction is a handful of statements on a pooled connection that the
next step is queued for; reading the same wait, run and flow twice in it was a
third of those statements.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity
from app.modules.workflow.domain.workflow import WorkflowEntity


@dataclass(frozen=True, slots=True)
class ResumeTarget:
    #: The ACTIVE wait being resumed.
    wait: WorkflowRunWaitEntity
    #: Its run, read with ``get_for_update`` -- the lock the engine takes itself
    #: when this is None.
    locked_run: WorkflowRunEntity | None = None
    flow: WorkflowEntity | None = None

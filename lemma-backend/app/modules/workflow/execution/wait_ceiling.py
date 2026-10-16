"""How long a run may stay parked on one wait, and what it says when it gives up.

The ceiling exists to catch a hang: without it a step that never answers holds
its run open forever, and the run reads as still going rather than broken. Its
expiry is a ``workflow.wait_expire`` item written with the wait (and, for an
AGENT wait until agent finalize emits facts, the reconciliation sweep).

* A machine wait (function, decision, agent) gets
  ``WORKFLOW_WAIT_MAX_AGE_SECONDS``, or the step's own ``timeout_seconds`` when
  its author set one.
* A wait blocked on a person gets ``HUMAN_WAIT_CEILING_MULTIPLIER`` times that.
  Someone not answering overnight is not a fault, and a workflow that asks at
  six in the evening and kills itself at midnight is worse than one that reads
  as still waiting.
* TIME and QUEUED waits have none: a wait-until is meant to sit as long as it
  was told to, and a run waiting its turn has not hung.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import settings
from app.modules.workflow.domain.wait import WorkflowRunWaitEntity, WorkflowRunWaitType

#: Where an agent step's own timeout rides on its wait.
WAIT_TIMEOUT_SECONDS_KEY = "timeout_seconds"

HUMAN_WAIT_CEILING_MULTIPLIER = 12

_NO_CEILING = frozenset({WorkflowRunWaitType.TIME, WorkflowRunWaitType.QUEUED})


def step_timeout_seconds(wait: WorkflowRunWaitEntity) -> float | None:
    """The step's own timeout, when its author set one."""
    value = wait.payload.get(WAIT_TIMEOUT_SECONDS_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def ceiling_seconds(
    wait: WorkflowRunWaitEntity, *, human_paced: bool | None = None
) -> float | None:
    """The longest ``wait`` may stay ACTIVE, or None when it may wait forever.

    ``human_paced`` overrides the wait type's own answer -- an agent blocked on
    a person reports so through its status.
    """
    if wait.wait_type in _NO_CEILING:
        return None
    seconds = step_timeout_seconds(wait) or settings.workflow_wait_max_age_seconds
    paced_by_person = (
        wait.wait_type == WorkflowRunWaitType.HUMAN
        if human_paced is None
        else human_paced
    )
    return seconds * HUMAN_WAIT_CEILING_MULTIPLIER if paced_by_person else seconds


def expires_at(wait: WorkflowRunWaitEntity, *, now: datetime) -> datetime | None:
    seconds = ceiling_seconds(wait)
    if seconds is None:
        return None
    return (wait.created_at or now) + timedelta(seconds=seconds)


def duration_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{round(seconds / 60):g} min"
    return f"{seconds / 3600:g}h"


def expiry_error(wait: WorkflowRunWaitEntity, seconds: float) -> str:
    """Why the run stopped, in the terms of the thing that did not happen.

    A form says nobody answered: "human step did not finish" reads as a system
    fault, and this one is not.
    """
    within = duration_text(seconds)
    if wait.wait_type == WorkflowRunWaitType.HUMAN:
        return f"Nobody answered this form within {within}, so the run was stopped."
    return (
        f"{wait.wait_type.value.title()} step did not finish within {within} "
        "and was stopped."
    )

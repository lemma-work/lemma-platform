"""The work a suspended run is woken, asked about, or given up on by.

Every suspension writes its items in the transaction that writes the wait, so a
committed wait always has whatever ends it:

| Wait     | Items                                                            |
|----------|------------------------------------------------------------------|
| FUNCTION | ``function.run`` (written with the run), ``workflow.wait_expire`` |
| TIME     | ``workflow.resume`` at ``scheduled_at``                           |
| DECISION | ``workflow.decide``, ``workflow.wait_expire``                     |
| HUMAN    | ``workflow.notify_form`` when assigned, ``workflow.wait_expire``  |
| AGENT    | none yet: the agent's own run, and the AGENT branch of the sweep  |
| QUEUED   | none: admitted in the transaction that ends a run of the flow    |

A FUNCTION wait is woken by ``workflow.resume`` too, written through the facts
registry by the function run's own terminal transition.

The handlers live in ``events/run_work.py`` (FAST: resume, expire, start) and
``events/step_jobs.py`` (JOB: decide, notify). Writing a kind needs it
registered in the process, which the module's ``register_work`` hook does at
assembly; ``_registered`` makes these helpers independent of that order without
an import cycle (the handlers import the engine, which imports this).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.infrastructure.work import WorkWriter, pod_namespace
from app.modules.workflow.domain.wait import (
    WorkflowRunWaitEntity,
    WorkflowRunWaitType,
    wait_expire_key,
)
from app.modules.workflow.execution.wait_ceiling import expires_at

WORKFLOW_RESUME = "workflow.resume"
WORKFLOW_WAIT_EXPIRE = "workflow.wait_expire"
WORKFLOW_DECIDE = "workflow.decide"
WORKFLOW_NOTIFY_FORM = "workflow.notify_form"
#: Admit a workflow's parked runs into whatever room it has. Admission normally
#: happens in the transaction that frees the room; this is for room that
#: appears any other way -- a raised limit, the cutover.
WORKFLOW_ADMIT = "workflow.admit"


class ResumeWork(BaseModel):
    """Wake the run waiting on one thing.

    Either a timer (``wait_type`` + ``external_ref``), or a function run that
    ended -- the ``FunctionRunFinished`` fact itself is the payload, so its
    other fields are ignored here.
    """

    model_config = ConfigDict(extra="ignore")

    wait_type: WorkflowRunWaitType | None = None
    external_ref: str | None = None
    function_run_id: UUID | None = None


class WaitWork(BaseModel):
    wait_id: UUID


class DecisionWork(BaseModel):
    external_ref: str


class AdmitWork(BaseModel):
    flow_id: UUID


def _registered() -> None:
    import app.modules.workflow.events.run_work  # noqa: F401
    import app.modules.workflow.events.step_jobs  # noqa: F401


async def write_timer(
    work: WorkWriter, *, pod_id: UUID, external_ref: str, fire_at: datetime
) -> None:
    """Wake a TIME wait at ``fire_at``."""
    _registered()
    await work.add(
        WORKFLOW_RESUME,
        pod_namespace(pod_id),
        ResumeWork(wait_type=WorkflowRunWaitType.TIME, external_ref=external_ref),
        run_at=fire_at,
        dedup_key=f"workflow-resume:{external_ref}",
    )


async def write_decision(work: WorkWriter, *, pod_id: UUID, external_ref: str) -> None:
    """Ask the decision a DECISION wait is parked on."""
    _registered()
    await work.add(
        WORKFLOW_DECIDE,
        pod_namespace(pod_id),
        DecisionWork(external_ref=external_ref),
        dedup_key=f"workflow-decide:{external_ref}",
    )


async def write_wait_items(
    work: WorkWriter, wait: WorkflowRunWaitEntity, *, now: datetime
) -> None:
    """The items every new wait gets from the engine: its expiry, its notice."""
    _registered()
    ns = pod_namespace(wait.pod_id)
    if wait.wait_type is not WorkflowRunWaitType.AGENT:
        # An AGENT wait's expiry depends on why the agent is waiting, which
        # only its status says; the sweep keeps that branch until agent
        # finalize emits facts.
        deadline = expires_at(wait, now=now)
        if deadline is not None:
            await work.add(
                WORKFLOW_WAIT_EXPIRE,
                ns,
                WaitWork(wait_id=wait.id),
                run_at=deadline,
                dedup_key=wait_expire_key(wait.id),
            )
    if (
        wait.wait_type is WorkflowRunWaitType.HUMAN
        and wait.assigned_pod_member_id is not None
    ):
        await work.add(
            WORKFLOW_NOTIFY_FORM,
            ns,
            WaitWork(wait_id=wait.id),
            dedup_key=f"workflow-notify:{wait.id}",
        )


async def write_admission(work: WorkWriter, *, pod_id: UUID, flow_id: UUID) -> None:
    _registered()
    await work.add(
        WORKFLOW_ADMIT,
        pod_namespace(pod_id),
        AdmitWork(flow_id=flow_id),
        dedup_key=f"workflow-admit:{flow_id}",
    )

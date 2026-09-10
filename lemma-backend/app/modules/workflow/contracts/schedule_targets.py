"""A workflow, as the thing a schedule fires.

The other half of what `app/composition/schedule_targets.py` did, which built
this module's repository from outside it and read four fields off the entity to
decide what a schedule may do with them.

Three of those four are the reason this is not a plain id-and-name lookup.
`is_global_workflow` decides whose schedule this is -- a GLOBAL workflow's
schedule belongs to the pod, a USER one's to whoever made it -- and the two
event-trigger fields are what a schedule created from a workflow's own EVENT
start config is routed on. All three are `workflow`'s own vocabulary, and
flattening them into `ScheduleTarget` here is what keeps `schedule` from having
to know what `EventWorkflowStartConfig` is.

A submodule rather than `contracts/__init__`, like its siblings: this reaches
the repository layer, and `contracts/__init__` is imported by anything that
wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.contracts.targets import ScheduleTarget
from app.modules.workflow.domain.start import (
    EventWorkflowStartConfig,
    WorkflowStartType,
)
from app.modules.workflow.domain.workflow import WorkflowEntity, WorkflowMode
from app.modules.workflow.infrastructure.repositories.workflow_repository import (
    SqlAlchemyWorkflowRepository,
)


async def workflow_schedule_target(
    uow: SqlAlchemyUnitOfWork, workflow_id: UUID
) -> ScheduleTarget | None:
    """The workflow with this id, or ``None`` when it no longer exists."""
    return _target(await SqlAlchemyWorkflowRepository(uow).get(workflow_id))


async def workflow_schedule_target_by_name(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, name: str
) -> ScheduleTarget | None:
    """The pod's workflow of this name, or ``None`` when it has none."""
    return _target(await SqlAlchemyWorkflowRepository(uow).get_by_name(pod_id, name))


def _target(workflow: WorkflowEntity | None) -> ScheduleTarget | None:
    if workflow is None:
        return None
    trigger_id = None
    trigger_config = None
    if workflow.start is not None and workflow.start.type is WorkflowStartType.EVENT:
        config = workflow.start.config
        if isinstance(config, EventWorkflowStartConfig):
            trigger_id = config.connector_trigger_id
            trigger_config = dict(config.trigger_config or {})
    return ScheduleTarget(
        id=workflow.id,
        pod_id=workflow.pod_id,
        name=workflow.name,
        is_global_workflow=workflow.mode is WorkflowMode.GLOBAL,
        event_trigger_id=trigger_id,
        event_trigger_config=trigger_config,
    )


__all__ = ["workflow_schedule_target", "workflow_schedule_target_by_name"]

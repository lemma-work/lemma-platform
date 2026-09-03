"""What another module does to a pod's workflows when it builds one.

Four operations, not `WorkflowService`. `workflow_exists` is the one the bundle
applier actually wanted: it creates a workflow once by name and never updates
it, so the entity it was fetching only ever answered a yes/no question.

A submodule rather than `contracts/__init__`: this reaches the service layer,
and `contracts/__init__` is imported by anything that wants any contract at all.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.workflow.api.dependencies import get_workflow_service
from app.modules.workflow.domain.workflow import WorkflowEntity


async def list_workflow_names(
    uow, *, pod_id: UUID, user_id: UUID, ctx: Context
) -> list[str]:
    """Every workflow in the pod this reader may see."""
    workflows, _ = await get_workflow_service(uow).list_workflows(
        pod_id, limit=1000, requester_user_id=user_id, ctx=ctx
    )
    return [str(workflow.name or "") for workflow in workflows]


async def get_workflow(
    uow, *, pod_id: UUID, name: str, user_id: UUID | None, ctx: Context
) -> WorkflowEntity | None:
    """The named workflow, or ``None`` when the pod does not have one."""
    return await get_workflow_service(uow).get_workflow_by_name(
        pod_id, name, requester_user_id=user_id, ctx=ctx
    )


async def workflow_exists(uow, *, pod_id: UUID, name: str, ctx: Context) -> bool:
    """Whether the pod already holds a workflow under this name."""
    workflow = await get_workflow_service(uow).get_workflow_by_name(
        pod_id, name, ctx=ctx
    )
    return workflow is not None


async def create_workflow(
    uow,
    *,
    pod_id: UUID,
    name: str,
    description: str | None,
    icon_url: str | None,
    start: object,
    mode: str,
    visibility: str | None,
    nodes: list[object] | None,
    edges: list[object] | None,
    user_id: UUID,
    ctx: Context,
) -> WorkflowEntity:
    """Create a workflow with its graph."""
    return await get_workflow_service(uow).create_workflow(
        pod_id=pod_id,
        name=name,
        description=description,
        icon_url=icon_url,
        start=start,
        mode=mode,
        visibility=visibility,
        nodes=nodes,
        edges=edges,
        requester_user_id=user_id,
        ctx=ctx,
    )


__all__ = [
    "create_workflow",
    "get_workflow",
    "list_workflow_names",
    "workflow_exists",
]

"""The workflow waits assigned to one person, across every pod they are in.

``GET /pods/{pod_id}/workflow-runs/waiting/assigned-to-me`` answers for one
pod; an organization's overview wants every pod's at once, and asking per pod
is the fan-out it exists to remove. One statement here, on
``ix_workflow_run_waits_assignee_status``.

**Being the named assignee is the grant**, exactly as it is per pod: every wait
here was assigned to one of this person's own pod memberships, and answering it
is the point. So there is no workflow read check -- a RESTRICTED workflow the
person cannot otherwise open still shows the form it asked them to fill in.
The caller passes the memberships, which it has resolved for the pods the
person may open; a membership in a deleted pod is never among them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.workflow.domain.wait import WorkflowRunWaitStatus
from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunWaitModel,
)


@dataclass(frozen=True, slots=True)
class AssignedWait:
    """An active wait assigned to the person, and enough to name and open it."""

    wait_id: UUID
    run_id: UUID
    pod_id: UUID
    workflow_id: UUID
    workflow_name: str
    node_id: str
    #: HUMAN for a form to fill in; DECISION for a question to decide.
    wait_type: str
    #: What it asks, in the author's words: a decision's question, or the
    #: form's own title. None when it says neither.
    question: str | None
    #: The conversation the wait points at, when it points at one.
    external_ref: str | None
    created_at: datetime


async def list_waits_assigned_to(
    session: AsyncSession,
    *,
    pod_member_ids: Sequence[UUID],
    limit: int,
) -> tuple[list[AssignedWait], bool]:
    """Active waits assigned to any of these memberships, longest-waiting first.

    Whether more were waiting than ``limit`` comes back beside the page.
    """
    if not pod_member_ids:
        return [], False
    wait = WorkflowRunWaitModel
    question = func.coalesce(
        wait.payload[("schema", "properties", "answer", "description")].astext,
        wait.payload[("input_schema", "title")].astext,
    )
    rows = (
        await session.execute(
            select(
                wait.id,
                wait.run_id,
                wait.pod_id,
                wait.flow_id,
                WorkflowModel.name,
                wait.node_id,
                wait.wait_type,
                question.label("question"),
                wait.external_ref,
                wait.created_at,
            )
            .join(WorkflowModel, WorkflowModel.id == wait.flow_id)
            .where(
                wait.assigned_pod_member_id.in_(list(pod_member_ids)),
                wait.status == WorkflowRunWaitStatus.ACTIVE.value,
            )
            .order_by(wait.created_at, wait.id)
            .limit(limit + 1)
        )
    ).all()
    return [
        AssignedWait(
            wait_id=wait_id,
            run_id=run_id,
            pod_id=pod_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            node_id=node_id,
            wait_type=str(wait_type),
            question=asked.strip() if asked and asked.strip() else None,
            external_ref=external_ref,
            created_at=created_at,
        )
        for (
            wait_id,
            run_id,
            pod_id,
            workflow_id,
            workflow_name,
            node_id,
            wait_type,
            asked,
            external_ref,
            created_at,
        ) in rows[:limit]
    ], len(rows) > limit


__all__ = ["AssignedWait", "list_waits_assigned_to"]

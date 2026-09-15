"""What another module may know about a pod's workflows, as a listing.

The agent's runtime brief names every resource an agent can reach, and workflows
were the one automation primitive missing from it: an agent could see the
functions and the schedules but not the processes wired between them, so it
proposed building one that already existed.

A read of three columns, not the graph. Anything that needs the nodes asks
`workflow` properly.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.modules.workflow.infrastructure.models import WorkflowModel


@dataclass(frozen=True, slots=True)
class PodWorkflowSummary:
    name: str
    description: str | None
    is_active: bool


async def list_workflow_summaries(
    *, session, pod_id: UUID, limit: int
) -> tuple[list[PodWorkflowSummary], int]:
    """This pod's workflows, newest first, plus how many there are in total.

    The total is what lets a capped listing say what it left out; a brief that
    silently stops at its cap tells the agent a workflow does not exist, and an
    agent that believes that does not go looking.
    """
    total = (
        await session.execute(
            select(func.count())
            .select_from(WorkflowModel)
            .where(WorkflowModel.pod_id == pod_id)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(
                WorkflowModel.name,
                WorkflowModel.description,
                WorkflowModel.is_active,
            )
            .where(WorkflowModel.pod_id == pod_id)
            .order_by(WorkflowModel.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        PodWorkflowSummary(name=name, description=description, is_active=is_active)
        for name, description, is_active in rows
    ], int(total)

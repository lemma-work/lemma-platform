"""What another module may know about a pod's workflows, as a listing.

The agent's runtime brief names every resource an agent can reach, and workflows
were the one automation primitive missing from it: an agent could see the
functions and the schedules but not the processes wired between them, so it
proposed building one that already existed.

**Visibility is enforced, not assumed.** A workflow carries its own visibility
and owner, so membership in a pod does not entitle you to read every workflow in
it. This delegates to the repository's own ``list_summaries_visible_by_pod``
rather than reimplementing the filter, because a second copy of an authorization
rule is a second place for it to be wrong -- which is exactly what the first
version of this file was, selecting on ``pod_id`` alone.

The count is taken through the same filter as the rows. A total computed over
everything would tell the reader how many workflows they cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.workflow.infrastructure.models import WorkflowModel
from app.modules.workflow.infrastructure.repositories.workflow_repository import (
    SqlAlchemyWorkflowRepository,
)


@dataclass(frozen=True, slots=True)
class PodWorkflowSummary:
    name: str
    description: str | None
    is_active: bool


async def list_workflow_summaries(
    *, uow, pod_id: UUID, ctx: Context, limit: int
) -> tuple[list[PodWorkflowSummary], int]:
    """The workflows this context may read, plus how many there are in total.

    The total is what lets a capped listing say what it left out; a brief that
    silently stops at its cap tells the agent a workflow does not exist, and an
    agent that believes that does not go looking.
    """
    repository = SqlAlchemyWorkflowRepository(uow)
    summaries, _ = await repository.list_summaries_visible_by_pod(
        pod_id, ctx=ctx, limit=limit
    )

    readable = allowed_actions_contains(
        allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.WORKFLOW,
            resource_id_col=WorkflowModel.id,
            pod_id_col=WorkflowModel.pod_id,
            owner_user_id_col=WorkflowModel.user_id,
            visibility_col=WorkflowModel.visibility,
        ),
        Permissions.WORKFLOW_READ,
    )
    total = (
        await uow.session.execute(
            select(func.count())
            .select_from(WorkflowModel)
            .where(WorkflowModel.pod_id == pod_id, readable)
        )
    ).scalar_one()

    return [
        PodWorkflowSummary(
            name=summary.name,
            description=summary.description,
            is_active=getattr(summary, "is_active", True),
        )
        for summary in summaries
    ], int(total)

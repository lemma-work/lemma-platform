"""How much each workflow has run lately, across many pods at once.

A pod's sidebar shows its own work as one line per automation: the workflow,
its newest run, and how many times it ran lately. Asked per workflow that is a
request per workflow per pod; this is one statement for every pod given, over
``ix_workflow_flow_runs_pod_created``, reading only the runs inside the window.

**Visibility is enforced, not assumed.** A workflow carries its own visibility
and owner, so the rows are filtered by the same ``allowed_actions_expr`` the
workflow listings use, against each pod's own context: the caller's
permissions in pod A say nothing about pod B. A run is readable by whoever may
read its workflow (``WorkflowEngine.get_run``), so every run of a readable
workflow counts, whoever started it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Subquery, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.context import Context, ResourceType
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunModel,
)


@dataclass(frozen=True, slots=True)
class WorkflowRunActivity:
    """One workflow's runs inside the window: how many, and the newest."""

    pod_id: UUID
    workflow_id: UUID
    workflow_name: str
    run_count: int
    latest_run_id: UUID
    latest_run_status: str
    latest_run_at: datetime


def _readable_in_their_pods(contexts: Mapping[UUID, Context]) -> ColumnElement[bool]:
    """Workflows each pod's own viewer may read, as one WHERE-able expression."""
    return or_(
        *[
            and_(
                WorkflowModel.pod_id == pod_id,
                allowed_actions_contains(
                    allowed_actions_expr(
                        ctx=ctx,
                        resource_type=ResourceType.WORKFLOW,
                        resource_id_col=WorkflowModel.id,
                        pod_id_col=WorkflowModel.pod_id,
                        owner_user_id_col=WorkflowModel.user_id,
                        visibility_col=WorkflowModel.visibility,
                    ),
                    Permissions.WORKFLOW_READ,
                ),
            )
            for pod_id, ctx in contexts.items()
        ]
    )


async def workflow_run_activity_by_pod(
    session: AsyncSession,
    *,
    contexts: Mapping[UUID, Context],
    since: datetime,
    per_pod: int,
) -> dict[UUID, list[WorkflowRunActivity]]:
    """Each pod's workflows that ran since ``since``, the latest-run first.

    ``per_pod`` caps each pod rather than the whole answer, so one pod with a
    hundred busy workflows cannot push every other pod's out. Pods with nothing
    are absent; read through ``.get(pod_id, [])``.
    """
    if not contexts:
        return {}
    latest = latest_runs_ranked_by_pod(contexts, since=since)
    rows = (
        await session.execute(
            select(
                latest.c.pod_id,
                latest.c.flow_id,
                latest.c.name,
                latest.c.runs,
                latest.c.id,
                latest.c.status,
                latest.c.created_at,
            )
            .where(latest.c.rank <= per_pod)
            .order_by(latest.c.pod_id, latest.c.created_at.desc(), latest.c.id.desc())
            # The rank already caps each pod; the ceiling on the whole read is
            # written out so it does not have to be reconstructed from it.
            .limit(per_pod * len(contexts))
        )
    ).all()
    by_pod: dict[UUID, list[WorkflowRunActivity]] = {}
    for pod_id, flow_id, name, runs, run_id, status, created_at in rows:
        by_pod.setdefault(pod_id, []).append(
            WorkflowRunActivity(
                pod_id=pod_id,
                workflow_id=flow_id,
                workflow_name=name,
                run_count=int(runs),
                latest_run_id=run_id,
                latest_run_status=str(status),
                latest_run_at=created_at,
            )
        )
    return by_pod


def latest_runs_ranked_by_pod(
    contexts: Mapping[UUID, Context], *, since: datetime
) -> Subquery:
    """Each readable workflow's newest run since ``since`` with its run count,
    numbered within its pod by recency -- the statement
    ``workflow_run_activity_by_pod`` caps.

    Two windows: one over each workflow's runs picks the newest and counts
    them, one over each pod's workflows ranks them, so a caller can cap each
    pod rather than the whole answer.
    """
    run = WorkflowRunModel
    newest_first = (run.created_at.desc(), run.id.desc())
    recent = (
        select(
            run.id,
            run.flow_id,
            run.pod_id,
            run.status,
            run.created_at,
            func.row_number()
            .over(partition_by=run.flow_id, order_by=newest_first)
            .label("newest"),
            func.count().over(partition_by=run.flow_id).label("runs"),
        )
        .where(run.pod_id.in_(list(contexts)), run.created_at >= since)
        .subquery("recent")
    )
    return (
        select(
            recent.c.pod_id,
            recent.c.flow_id,
            WorkflowModel.name,
            recent.c.runs,
            recent.c.id,
            recent.c.status,
            recent.c.created_at,
            func.row_number()
            .over(
                partition_by=recent.c.pod_id,
                order_by=(recent.c.created_at.desc(), recent.c.id.desc()),
            )
            .label("rank"),
        )
        .join(WorkflowModel, WorkflowModel.id == recent.c.flow_id)
        .where(recent.c.newest == 1, _readable_in_their_pods(contexts))
        .subquery("latest")
    )


__all__ = ["WorkflowRunActivity", "workflow_run_activity_by_pod"]

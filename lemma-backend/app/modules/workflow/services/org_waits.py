"""Workflow forms on the ``org`` topic: what waits on the reader.

A run stopped at a form assigned to a pod member is told to that member alone
-- the person ``GET …/workflow-runs/waiting/assigned-to-me`` lists it for -- as
``waiting.started`` (``kind: "workflow"``), and as ``waiting.stopped`` once it
is submitted, cancelled or expires. A form with nobody assigned is told to
nobody. The wait row moving says when to read (``wait``,
``wait_repository``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.org_feeds import Audience, OrgItem, PodItems
from app.modules.pod.contracts.members import pod_member_user_ids
from app.modules.workflow.domain.wait import WorkflowRunWaitStatus, WorkflowRunWaitType
from app.modules.workflow.infrastructure.models import (
    WorkflowModel,
    WorkflowRunWaitModel,
)

#: The pod signal a human wait's row moving sends.
WAIT_SIGNAL = "wait"
#: Most open forms one read returns, across the pods it covers.
MAX_WAITS = 2000


def _waiting(
    *, wait_id: UUID, run_id: UUID, workflow_id: UUID, name: str, assignee: UUID
) -> OrgItem:
    said = {
        "kind": "workflow",
        "wait_id": str(wait_id),
        "workflow_run_id": str(run_id),
        "workflow_id": str(workflow_id),
        "workflow_name": name,
    }
    return OrgItem(
        audience=Audience(users=frozenset({assignee})),
        version="waiting",
        held={"type": "waiting.started", **said},
        ended={"type": "waiting.stopped", **said},
    )


class AssignedWaits:
    name = "workflow.assigned_waits"
    kinds = frozenset({WAIT_SIGNAL})

    async def read(self, pod_ids: Sequence[UUID]) -> Mapping[UUID, PodItems]:
        wait, flow = WorkflowRunWaitModel, WorkflowModel
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            rows = (
                await uow.session.execute(
                    select(
                        wait.pod_id,
                        wait.id,
                        wait.run_id,
                        wait.flow_id,
                        flow.name,
                        wait.assigned_pod_member_id,
                    )
                    .join(flow, flow.id == wait.flow_id)
                    .where(
                        wait.pod_id.in_(sorted(set(pod_ids))),
                        wait.status == WorkflowRunWaitStatus.ACTIVE.value,
                        wait.wait_type == WorkflowRunWaitType.HUMAN.value,
                        wait.assigned_pod_member_id.is_not(None),
                    )
                    .order_by(wait.id)
                    .limit(MAX_WAITS)
                )
            ).all()
            users = await pod_member_user_ids(
                uow.session, [row.assigned_pod_member_id for row in rows]
            )
        found: dict[UUID, dict[str, OrgItem]] = {}
        for pod_id, wait_id, run_id, workflow_id, name, member_id in rows:
            assignee = users.get(member_id)
            if assignee is None:
                continue
            found.setdefault(pod_id, {})[str(wait_id)] = _waiting(
                wait_id=wait_id,
                run_id=run_id,
                workflow_id=workflow_id,
                name=name,
                assignee=assignee,
            )
        return found


__all__ = ["AssignedWaits", "WAIT_SIGNAL"]

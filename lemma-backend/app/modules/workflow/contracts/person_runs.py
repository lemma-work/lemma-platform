"""Workflows, as an outside MCP client runs them for the person it acts as.

Four operations for the pod's MCP surface (``agent/services/pod_mcp_workflows``):
the pod's workflows and which the person may start, starting one, reading a
run, and the forms waiting on them. Answering a form is ``forms.submit_workflow_form``,
which already re-checks the assignee and the form's schema.

Each returns this module's own small shapes rather than the engine's
entities: the caller is another module's tool, and a run's full execution
context is not what a tool result should carry.

A submodule rather than `contracts/__init__`: this reaches the execution layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from app.modules.workflow.domain.run import WorkflowRunEntity


class WorkflowRefused(Exception):
    """A start or a read that cannot be made, with the reason to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class PodWorkflow:
    name: str
    description: str | None
    may_start: bool


@dataclass(frozen=True, slots=True)
class RunWait:
    """What a run is waiting on."""

    node_id: str
    #: HUMAN for a form; AGENT, FUNCTION, TIME, DECISION or QUEUED otherwise.
    kind: str
    #: The form's fields, title and instructions for a HUMAN wait.
    payload: dict[str, object]
    #: Whether the person is the one it waits on.
    on_this_person: bool


@dataclass(frozen=True, slots=True)
class PersonWorkflowRun:
    id: UUID
    workflow: str
    status: str
    started_by: UUID
    error: str | None
    waiting: RunWait | None
    #: Whether the person may read the run's workflow, which its own page
    #: needs, and run it, which submitting any of its forms needs -- in Lemma
    #: as here (``WorkflowEngine.submit_form`` checks it before the assignee).
    may_read_workflow: bool = False
    may_submit: bool = False


async def pod_workflows(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ctx: Context, limit: int
) -> list[PodWorkflow]:
    """The active workflows ``ctx`` may read, by name, saying which it may start."""
    from app.modules.workflow.infrastructure.repositories.workflow_repository import (
        SqlAlchemyWorkflowRepository,
    )

    summaries, _ = await SqlAlchemyWorkflowRepository(
        uow
    ).list_summaries_visible_by_pod(pod_id, ctx=ctx, limit=limit)
    return sorted(
        (
            PodWorkflow(
                name=summary.name,
                description=summary.description,
                may_start=Permissions.WORKFLOW_EXECUTE in summary.allowed_actions,
            )
            for summary in summaries
            if summary.is_active
        ),
        key=lambda workflow: workflow.name.casefold(),
    )


async def start_workflow_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    name: str,
    user_id: UUID,
    ctx: Context,
) -> PersonWorkflowRun:
    """Start ``name`` as the person; a workflow that begins with a form waits
    on it, since the form is how a run takes its input."""
    from app.modules.workflow.api.dependencies import build_workflow_engine
    from app.modules.workflow.infrastructure.repositories.workflow_repository import (
        SqlAlchemyWorkflowRepository,
    )

    workflow = await SqlAlchemyWorkflowRepository(uow).get_by_name(
        pod_id, name, ctx=ctx
    )
    if workflow is None or workflow.id is None:
        raise WorkflowRefused(f"No workflow you may start is called {name!r}.")
    engine = build_workflow_engine(uow)
    try:
        run = await engine.start_run(workflow.id, user_id, ctx=ctx)
    except (DomainError, ValueError) as exc:
        raise WorkflowRefused(str(exc)) from exc
    return await _person_run(uow, run, user_id=user_id, ctx=ctx)


async def workflow_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    run_id: UUID,
    user_id: UUID,
    ctx: Context,
) -> PersonWorkflowRun:
    """A run in this pod ``ctx`` may read, refused alike when it is missing,
    in another pod, or not the person's to see."""
    from app.modules.workflow.api.dependencies import build_workflow_engine

    try:
        run = await build_workflow_engine(uow).get_run(
            run_id, requester_user_id=user_id, ctx=ctx
        )
    except DomainError:
        run = None
    if run is None or run.pod_id != pod_id:
        raise WorkflowRefused("No workflow run you may read has that id.")
    return await _person_run(uow, run, user_id=user_id, ctx=ctx)


async def waiting_on(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    ctx: Context,
    limit: int,
) -> list[PersonWorkflowRun]:
    """The runs with a form waiting on the person, newest first.

    No per-run read check, as the API's own queue has none: being the named
    assignee is enough to be shown it, and a refusal on one restricted
    workflow must not empty the whole list. What ``ctx`` may do with each
    run's workflow is said on the run instead, from one read of exactly
    those workflows.
    """
    from app.modules.pod.contracts.members import pod_member_id
    from app.modules.workflow.infrastructure.repositories.run_repository import (
        SqlAlchemyWorkflowRunRepository,
    )
    from app.modules.workflow.infrastructure.repositories.wait_repository import (
        SqlAlchemyWorkflowRunWaitRepository,
    )

    member_id = await pod_member_id(uow, pod_id, user_id)
    if member_id is None:
        return []
    waits, _ = await SqlAlchemyWorkflowRunWaitRepository(uow).list_active_for_assignee(
        pod_id=pod_id, assigned_pod_member_id=member_id, limit=limit
    )
    runs = await SqlAlchemyWorkflowRunRepository(uow).list_summaries_by_ids(
        [wait.run_id for wait in waits]
    )
    access = await _workflow_access(uow, ctx, {wait.flow_id for wait in waits})
    return [
        PersonWorkflowRun(
            id=wait.run_id,
            workflow=access[wait.flow_id].name
            if wait.flow_id in access
            else "workflow",
            status=_value(runs[wait.run_id].status),
            started_by=runs[wait.run_id].user_id,
            error=runs[wait.run_id].error,
            waiting=RunWait(
                node_id=wait.node_id,
                kind=_value(wait.wait_type),
                payload=dict(wait.payload),
                on_this_person=True,
            ),
            may_read_workflow=wait.flow_id in access and access[wait.flow_id].may_read,
            may_submit=wait.flow_id in access and access[wait.flow_id].may_run,
        )
        for wait in waits
        if wait.run_id in runs
    ]


async def _person_run(
    uow: SqlAlchemyUnitOfWork,
    run: WorkflowRunEntity,
    *,
    user_id: UUID,
    ctx: Context,
) -> PersonWorkflowRun:
    from app.modules.pod.contracts.members import pod_member_id
    from app.modules.workflow.api.dependencies import build_workflow_engine

    wait = await build_workflow_engine(uow).get_active_wait(run.id)
    member_id = (
        await pod_member_id(uow, run.pod_id, user_id) if wait is not None else None
    )
    access = (await _workflow_access(uow, ctx, {run.flow_id})).get(run.flow_id)
    return PersonWorkflowRun(
        id=run.id,
        workflow=access.name if access else "workflow",
        may_read_workflow=access is not None and access.may_read,
        may_submit=access is not None and access.may_run,
        status=_value(run.status),
        started_by=run.user_id,
        error=run.error,
        waiting=(
            RunWait(
                node_id=wait.node_id,
                kind=_value(wait.wait_type),
                payload=dict(wait.payload),
                on_this_person=(
                    member_id is not None and wait.assigned_pod_member_id == member_id
                ),
            )
            if wait is not None
            else None
        ),
    )


@dataclass(frozen=True, slots=True)
class _WorkflowAccess:
    name: str
    may_read: bool
    may_run: bool


async def _workflow_access(
    uow: SqlAlchemyUnitOfWork, ctx: Context, flow_ids: set[UUID]
) -> dict[UUID, _WorkflowAccess]:
    """Each workflow's name, and what ``ctx`` may do with it, in one read.

    Asked of exactly these workflows rather than read off a listing: a listing
    is capped and shows only active workflows, and a run's workflow need be
    neither the first nor still active.
    """
    from sqlalchemy import select

    from app.core.authorization.context import ResourceType
    from app.core.authorization.sql_actions import allowed_actions_expr
    from app.modules.workflow.infrastructure.models import WorkflowModel

    if not flow_ids:
        return {}
    actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.WORKFLOW,
        resource_id_col=WorkflowModel.id,
        pod_id_col=WorkflowModel.pod_id,
        owner_user_id_col=WorkflowModel.user_id,
        visibility_col=WorkflowModel.visibility,
    )
    rows = await uow.session.execute(
        select(WorkflowModel.id, WorkflowModel.name, actions).where(
            WorkflowModel.id.in_(flow_ids)
        )
    )
    return {
        flow_id: _WorkflowAccess(
            name=name,
            may_read=Permissions.WORKFLOW_READ in (allowed or ()),
            may_run=Permissions.WORKFLOW_EXECUTE in (allowed or ()),
        )
        for flow_id, name, allowed in rows.all()
    }


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = [
    "PersonWorkflowRun",
    "PodWorkflow",
    "RunWait",
    "WorkflowRefused",
    "pod_workflows",
    "start_workflow_run",
    "waiting_on",
    "workflow_run",
]

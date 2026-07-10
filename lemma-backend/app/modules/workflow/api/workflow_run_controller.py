from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import PodContextDep
from app.composition.workflow_pod import PodMemberRepository
from app.modules.workflow.api.schemas import (
    WorkflowRunFormSubmitRequest,
    WorkflowRunResponse,
    WorkflowRunSummaryResponse,
    WorkflowRunWaitAssignment,
    WorkflowRunWaitAssignmentListResponse,
    WorkflowRunWaitResponse,
    run_response_from_domain,
)
from app.modules.workflow.domain.run import WorkflowRunEntity
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.modules.workflow.services.workflow_service import WorkflowService

# Setup templates (Adjust path relative to this file location)
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/pods/{pod_id}/workflow-runs", tags=["workflows"])


def _verify_pod(run: WorkflowRunEntity | None, pod_id: UUID):
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        )
    if run.pod_id != pod_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run does not belong to this pod",
        )


@router.post(
    "/{run_id}/form",
    response_model=WorkflowRunResponse,
    operation_id="workflow.run.form.submit",
    summary="Submit Workflow Run Form",
    description=(
        "Submit the form the run is waiting on. `node_id` must match the "
        "run's active HUMAN wait (409 when the run is not waiting on a form, "
        "422 on node mismatch, 403 when the wait is assigned to someone "
        "else). The submitted `inputs` become the form node's output, "
        "available to later nodes as `<node_id>.<field>`."
    ),
)
async def submit_workflow_run_form(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    run_id: UUID,
    data: WorkflowRunFormSubmitRequest,
) -> WorkflowRunResponse:
    engine = WorkflowEngine(uow)
    run = await engine.get_run(run_id, requester_user_id=user.id, ctx=ctx)
    _verify_pod(run, pod_id)

    async with uow:
        run = await engine.submit_form(
            run_id,
            data.node_id,
            data.inputs,
            requester_user_id=user.id,
            ctx=ctx,
        )
        active_wait = await engine.get_active_wait(run.id)
        return run_response_from_domain(run, active_wait)


@router.post(
    "/{run_id}/cancel",
    response_model=WorkflowRunResponse,
    operation_id="workflow.run.cancel",
    summary="Cancel Workflow Run",
    description=(
        "Cancel a non-terminal run. The active wait (if any) is cancelled in "
        "the same transaction; late completion events for cancelled waits are "
        "dropped. Cancelling a terminal run returns 409."
    ),
)
async def cancel_workflow_run(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    run_id: UUID,
) -> WorkflowRunResponse:
    engine = WorkflowEngine(uow)
    run = await engine.get_run(run_id, requester_user_id=user.id, ctx=ctx)
    _verify_pod(run, pod_id)

    async with uow:
        run = await engine.cancel_run(run_id, requester_user_id=user.id, ctx=ctx)
        return run_response_from_domain(run, None)


@router.get(
    "/waiting/assigned-to-me",
    response_model=WorkflowRunWaitAssignmentListResponse,
    operation_id="workflow.run.waiting_assigned_to_me",
    summary="List Workflow Runs Waiting For Current User",
    description=(
        "The current user's approval queue: active form waits assigned to "
        "them, with the owning run."
    ),
)
async def list_waiting_runs_assigned_to_me(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    limit: int = 100,
    page_token: str | None = None,
) -> WorkflowRunWaitAssignmentListResponse:
    cursor = parse_uuid_page_token(page_token)

    pod_member = await PodMemberRepository(uow).get_by_pod_and_user_id(pod_id, user.id)
    if pod_member is None:
        raise HTTPException(status_code=404, detail="Pod member not found")

    wait_repo = SqlAlchemyWorkflowRunWaitRepository(uow)
    waits, next_cursor = await wait_repo.list_active_for_assignee(
        pod_id=pod_id,
        assigned_pod_member_id=pod_member.id,
        limit=limit,
        cursor=cursor,
    )
    engine = WorkflowEngine(uow)
    items: list[WorkflowRunWaitAssignment] = []
    for wait in waits:
        run = await engine.get_run(wait.run_id, requester_user_id=user.id, ctx=ctx)
        if run is not None:
            items.append(
                WorkflowRunWaitAssignment(
                    wait=WorkflowRunWaitResponse.model_validate(wait),
                    run=WorkflowRunSummaryResponse.model_validate(run),
                )
            )

    return WorkflowRunWaitAssignmentListResponse(
        items=items,
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{run_id}",
    response_model=WorkflowRunResponse,
    operation_id="workflow.run.get",
    summary="Get Workflow Run",
    description=(
        "Get current state, context, step history, and the active wait (when "
        "WAITING) of a workflow run."
    ),
)
async def get_run(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    run_id: UUID,
) -> WorkflowRunResponse:
    engine = WorkflowEngine(uow)
    run = await engine.get_run(run_id, requester_user_id=user.id, ctx=ctx)
    _verify_pod(run, pod_id)
    assert run is not None
    active_wait = await engine.get_active_wait(run.id)
    return run_response_from_domain(run, active_wait)


@router.get(
    "/{run_id}/visualize",
    response_class=HTMLResponse,
    operation_id="workflow.run.visualize",
    summary="Visualize Workflow Run",
    description="Render an HTML view of a run overlaid on its workflow graph.",
)
async def visualize_flow_run(
    request: Request,
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    run_id: UUID,
):
    engine = WorkflowEngine(uow)
    run = await engine.get_run(run_id, requester_user_id=user.id, ctx=ctx)
    _verify_pod(run, pod_id)

    # We need the workflow definition to draw the graph
    workflow_service = WorkflowService(uow)
    workflow = await workflow_service.get_workflow(
        run.flow_id, requester_user_id=user.id, ctx=ctx
    )

    return templates.TemplateResponse(
        "workflow_run_view.html",
        {
            "request": request,
            "run": run.model_dump(mode="json"),
            "workflow": workflow.model_dump(mode="json") if workflow else None,
        },
    )

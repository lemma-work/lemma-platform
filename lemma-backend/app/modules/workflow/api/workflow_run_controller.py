import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.dependencies import PodContextDep
from app.core.authorization.permissions import Permissions
from app.composition.workflow_pod import PodMemberRepository
from app.modules.workflow.api.schemas import (
    WorkflowRunFormSubmitRequest,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowRunSummaryResponse,
    WorkflowRunWaitAssignment,
    WorkflowRunWaitAssignmentListResponse,
    WorkflowRunWaitResponse,
    run_response_from_domain,
)
from app.core.domain.realtime import RealtimeChannel
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.core.log.log import get_logger
from app.modules.workflow.domain.run import TERMINAL_STATUSES, WorkflowRunEntity
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.infrastructure.repositories import (
    SqlAlchemyWorkflowRunRepository,
    SqlAlchemyWorkflowRunWaitRepository,
)
from app.modules.workflow.infrastructure.run_channel import (
    encode_run_chunk,
    workflow_run_channel,
)
from app.modules.workflow.services.workflow_service import WorkflowService

# Setup templates (Adjust path relative to this file location)
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = get_logger(__name__)

WorkflowChannelDep = Annotated[RealtimeChannel, Depends(get_channel_service)]

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
    "",
    response_model=WorkflowRunListResponse,
    operation_id="workflow.run.list_for_pod",
    summary="List Workflow Runs In Pod",
    description=(
        "Recent runs across every workflow in the pod, newest first. Exists so "
        "an index that wants 'what has been happening here' makes one request "
        "instead of one per workflow. Filter with `status` (repeatable)."
    ),
)
async def list_pod_workflow_runs(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    limit: int = 50,
    status: list[str] | None = Query(default=None),
    page_token: str | None = None,
) -> WorkflowRunListResponse:
    await ctx.require(
        Permissions.WORKFLOW_READ,
        ResourceRef(
            resource_type=ResourceType.POD,
            resource_id=pod_id,
            pod_id=pod_id,
        ),
    )
    cursor = parse_uuid_page_token(page_token)
    runs, next_cursor = await SqlAlchemyWorkflowRunRepository(uow).list_by_pod(
        pod_id,
        limit=min(limit, 200),
        cursor=cursor,
        statuses=[value.upper() for value in status] if status else None,
    )
    return WorkflowRunListResponse(
        items=[WorkflowRunSummaryResponse.model_validate(run) for run in runs],
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
    "/{run_id}/stream",
    operation_id="workflow.run.stream",
    summary="Stream Workflow Run",
    description=(
        "Server-sent events carrying the run's state as it advances. The first "
        "frame is the current run, so a client needs no separate GET; each "
        "later frame is the whole run again rather than a diff, which makes "
        "reconnecting a matter of replacing state. A `completed` frame is sent "
        "when the run reaches a terminal status, after which the stream closes. "
        "Polling remains a valid fallback."
    ),
)
async def stream_workflow_run(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    channel_service: WorkflowChannelDep,
    pod_id: UUID,
    run_id: UUID,
) -> StreamingResponse:
    engine = WorkflowEngine(uow)
    run = await engine.get_run(run_id, requester_user_id=user.id, ctx=ctx)
    _verify_pod(run, pod_id)
    assert run is not None
    active_wait = await engine.get_active_wait(run.id)
    opening = run_response_from_domain(run, active_wait)
    is_terminal = run.status in TERMINAL_STATUSES

    # Subscribe before yielding the opening frame, so a transition landing
    # between the two is delivered rather than lost.
    subscription = channel_service.subscribe([workflow_run_channel(run_id)])

    async def event_generator() -> AsyncGenerator[str, None]:
        iterator = await subscription.__aenter__()
        try:
            yield encode_run_chunk(
                event_type="completed" if is_terminal else "run",
                data=opening.model_dump(mode="json"),
            )
            if is_terminal:
                return
            async for message in iterator:
                payload = _decode_channel_message(message)
                if payload is None:
                    continue
                yield encode_run_chunk(
                    event_type=str(payload.get("type") or "run"),
                    data=payload.get("data"),
                )
                if payload.get("type") == "completed":
                    return
        except Exception:
            logger.error(
                "workflow.run.stream_failed", run_id=str(run_id), exc_info=True
            )
            yield encode_run_chunk(
                event_type="error",
                data="Realtime stream interrupted. Reconnect to continue.",
            )
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await subscription.__aexit__(None, None, None)
                except Exception:
                    pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _decode_channel_message(message: object) -> dict | None:
    try:
        payload = json.loads(message) if isinstance(message, (str, bytes)) else message
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


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

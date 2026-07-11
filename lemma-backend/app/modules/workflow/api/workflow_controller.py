from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.modules.workflow.api.dependencies import (
    WorkflowServiceDep,
    WorkflowResourceDeleteDep,
    WorkflowResourceEditorDep,
    WorkflowResourceExecuteDep,
    WorkflowResourceViewerDep,
    WorkflowViewerDep,
)
from app.core.api.pagination import parse_uuid_page_token
from app.modules.workflow.api.schemas import (
    WorkflowDetailResponse,
    WorkflowSummaryResponse,
    WorkflowCreateRequest,
    WorkflowGraphUpdateRequest,
    WorkflowListResponse,
    WorkflowRunListResponse,
    WorkflowRunResponse,
    WorkflowUpdateRequest,
    workflow_response_from_domain,
    run_response_from_domain,
    workflow_start_input_to_domain,
)
from app.modules.workflow.domain.workflow import WorkflowEntity, WorkflowUpdateEntity
from app.modules.workflow.execution.engine import WorkflowEngine

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/pods/{pod_id}/workflows", tags=["workflows"])


def _verify_pod(workflow: WorkflowEntity | None, pod_id: UUID):
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found"
        )
    if workflow.pod_id != pod_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workflow does not belong to this pod",
        )


async def _flow_detail_response(
    workflow: WorkflowEntity,
) -> WorkflowDetailResponse:
    return WorkflowDetailResponse(
        **workflow_response_from_domain(workflow).model_dump(),
        allowed_actions=workflow.allowed_actions,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowDetailResponse,
    operation_id="workflow.create",
    summary="Create Workflow",
    description=(
        "Create a workflow definition. The graph (`nodes`/`edges`) can be included "
        "in this call to create a ready-to-run workflow in one step, or omitted to "
        "create a shell and upload the graph later with `workflow.graph.update`."
    ),
)
async def create_workflow(
    user: CurrentUser,
    pod_id: UUID,
    data: WorkflowCreateRequest,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> WorkflowDetailResponse:
    workflow = await service.create_workflow(
        pod_id,
        data.name,
        data.description,
        data.icon_url,
        workflow_start_input_to_domain(data.start),
        data.mode,
        visibility=data.visibility,
        nodes=data.nodes,
        edges=data.edges,
        requester_user_id=user.id,
        ctx=ctx,
    )
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow.name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    assert workflow is not None
    return await _flow_detail_response(workflow)


@router.get(
    "/{workflow_name}",
    response_model=WorkflowDetailResponse,
    operation_id="workflow.get",
    summary="Get Workflow",
    description="Get a single workflow definition including graph and start configuration.",
    dependencies=[WorkflowResourceViewerDep],
)
async def get_workflow(
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> WorkflowDetailResponse:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None
    return await _flow_detail_response(workflow)


@router.patch(
    "/{workflow_name}",
    response_model=WorkflowDetailResponse,
    operation_id="workflow.update",
    summary="Update Workflow Metadata",
    description=(
        "Update workflow-level metadata such as description and schedule mode. "
        "Workflow names are immutable after creation. Use `workflow.graph.update` for nodes and edges."
    ),
    dependencies=[WorkflowResourceEditorDep],
)
async def update_workflow(
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    data: WorkflowUpdateRequest,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> WorkflowDetailResponse:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None

    update_payload = data.model_dump(exclude_unset=True, exclude={"start"})
    if "start" in data.model_fields_set:
        update_payload["start"] = workflow_start_input_to_domain(data.start)

    updated = await service.update_workflow(
        workflow.id,
        WorkflowUpdateEntity(**update_payload),
        requester_user_id=user.id,
        ctx=ctx,
    )
    updated = await service.get_workflow(workflow.id, requester_user_id=user.id, ctx=ctx) or updated
    return await _flow_detail_response(updated)


@router.put(
    "/{workflow_name}/graph",
    response_model=WorkflowDetailResponse,
    operation_id="workflow.graph.update",
    summary="Update Workflow Graph",
    description=(
        "Replace the workflow graph. Agent/function node `input_mapping` entries must use "
        'explicit typed bindings. Use `{type: "expression", value: '
        '"start.payload.issue.key"}` for context lookups and '
        '`{type: "literal", value: "abc"}` for fixed JSON values.'
    ),
    dependencies=[WorkflowResourceEditorDep],
)
async def update_workflow_graph(
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    data: WorkflowGraphUpdateRequest,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> WorkflowDetailResponse:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None

    updated = await service.update_workflow_graph(
        workflow.id,
        data.nodes,
        data.edges,
        workflow_start_input_to_domain(data.start),
        requester_user_id=user.id,
        ctx=ctx,
    )
    updated = await service.get_workflow(workflow.id, requester_user_id=user.id, ctx=ctx) or updated
    return await _flow_detail_response(updated)


@router.get(
    "",
    response_model=WorkflowListResponse,
    operation_id="workflow.list",
    summary="List Workflows",
    description="List all workflows in a pod.",
    dependencies=[WorkflowViewerDep],
)
async def list_workflows(
    user: CurrentUser,
    pod_id: UUID,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
    limit: int = 100,
    page_token: str | None = None,
) -> WorkflowListResponse:
    cursor = parse_uuid_page_token(page_token)

    summaries, next_cursor = await service.list_workflow_summaries(
        pod_id,
        limit=limit,
        cursor=cursor,
        requester_user_id=user.id,
        ctx=ctx,
    )
    return WorkflowListResponse(
        items=[WorkflowSummaryResponse.model_validate(summary) for summary in summaries],
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.delete(
    "/{workflow_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="workflow.delete",
    summary="Delete Workflow",
    description="Delete a workflow definition.",
    dependencies=[WorkflowResourceDeleteDep],
)
async def delete_workflow(
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> None:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None
    await service.delete_workflow(workflow.id, requester_user_id=user.id, ctx=ctx)


@router.post(
    "/{workflow_name}/runs",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowRunResponse,
    operation_id="workflow.run.create",
    summary="Create Workflow Run",
    description=(
        "Create a new run for this workflow. Takes no request body: if the "
        "workflow's entry node is a FORM node the run is created WAITING on it "
        "(see `active_wait` in the response) and input is submitted via "
        "`workflow.run.form.submit`; otherwise the run executes immediately. "
        "Trigger payloads for scheduled/event/datastore starts are supplied by "
        "the platform, not through this endpoint."
    ),
    dependencies=[WorkflowResourceExecuteDep],
)
async def create_workflow_run(
    uow: UoWDep,
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
) -> WorkflowRunResponse:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None

    engine = WorkflowEngine(uow)
    run = await engine.start_run(workflow.id, user.id, ctx=ctx)
    active_wait = await engine.get_active_wait(run.id)
    return run_response_from_domain(run, active_wait)


@router.get(
    "/{workflow_name}/runs",
    response_model=WorkflowRunListResponse,
    operation_id="workflow.run.list",
    summary="List Workflow Runs",
    description="List recent runs for a given workflow.",
    dependencies=[WorkflowResourceViewerDep],
)
async def list_workflow_runs(
    uow: UoWDep,
    user: CurrentUser,
    ctx: PodContextDep,
    pod_id: UUID,
    workflow_name: str,
    service: WorkflowServiceDep,
    limit: int = 100,
    page_token: str | None = None,
) -> WorkflowRunListResponse:
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)
    assert workflow is not None

    cursor = parse_uuid_page_token(page_token)

    engine = WorkflowEngine(uow)
    runs, next_cursor = await engine.list_runs(
        workflow.id,
        limit=limit,
        cursor=cursor,
        requester_user_id=user.id,
        ctx=ctx,
    )
    return WorkflowRunListResponse(
        items=runs,
        limit=limit,
        next_page_token=str(next_cursor) if next_cursor else None,
    )


@router.get(
    "/{workflow_name}/visualize",
    response_class=HTMLResponse,
    operation_id="workflow.visualize",
    summary="Visualize Workflow",
    description="Render an HTML visualization for debugging workflow graph structure.",
    dependencies=[WorkflowResourceViewerDep],
)
async def visualize_workflow(
    request: Request,
    user: CurrentUser,
    pod_id: UUID,
    workflow_name: str,
    service: WorkflowServiceDep,
    ctx: PodContextDep,
):
    workflow = await service.get_workflow_by_name(
        pod_id,
        workflow_name,
        requester_user_id=user.id,
        ctx=ctx,
    )
    _verify_pod(workflow, pod_id)

    return templates.TemplateResponse(
        "workflow_view.html",
        {"request": request, "workflow": workflow.model_dump(mode="json")},
    )

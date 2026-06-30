"""Function API controller."""

from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request, status

from app.core.api.dependencies import UoWDep, get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.authorization.grants import (
    list_grantee_resource_grants,
    normalize_pod_resource_grants,
    replace_grantee_resource_grants,
    validate_pod_resource_grant_permissions,
)
from app.core.authorization.dependencies import PodContextDep
from app.core.api.pagination import parse_uuid_page_token
from app.core.helpers.slug import normalize_resource_name

from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.function.api.schemas.function_schemas import (
    CreateFunctionRequest,
    ExecuteFunctionRequest,
    FunctionActionResponse,
    FunctionDetailResponse,
    FunctionListResponse,
    FunctionMessageResponse,
    FunctionPermissionsReplaceRequest,
    FunctionPermissionsResponse,
    FunctionResponse,
    FunctionResourcePermissionResponse,
    FunctionRunListResponse,
    FunctionSummaryResponse,
    FunctionRunResponse,
    FunctionRunSummaryResponse,
    UpdateFunctionRequest,
)
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionUpdateEntity,
)
from app.modules.function.api.dependencies import (
    FunctionServiceDep,
    FunctionUseCasesDep,
    FunctionViewerDep,
    FunctionResourceDeleteDep,
    FunctionResourceEditorDep,
    FunctionResourceAdminDep,
    FunctionResourceViewerDep,
)
from app.modules.workspace.services.workspace_tool_runtime import (
    invalidate_function_workspace_env_cache,
)

router = APIRouter(
    prefix="/pods/{pod_id}/functions",
    tags=["Functions"],
    redirect_slashes=False,
)


def _to_function_response(function: FunctionEntity) -> FunctionResponse:
    payload = function.model_dump()
    return FunctionResponse.model_validate(payload)


async def _function_action_response(
    function: FunctionEntity,
) -> FunctionActionResponse:
    return FunctionActionResponse(
        **_to_function_response(function).model_dump(),
        allowed_actions=function.allowed_actions,
    )


def _function_summary_response(function: FunctionEntity) -> FunctionSummaryResponse:
    # `allowed_actions` lives on the entity; from_attributes picks up the rest and
    # drops the heavy input/output/config schemas + code.
    return FunctionSummaryResponse.model_validate(function)


@router.post(
    "",
    response_model=FunctionActionResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="function.create",
    summary="Create Function",
    description=(
        "Create a new function in a pod. Do not send input_schema, output_schema, "
        "or config_schema; the platform derives those schemas from the function "
        "code and returns them in the response."
    ),
)
async def create_function(
    request: Request,
    pod_id: UUID,
    data: CreateFunctionRequest,
    use_cases: FunctionUseCasesDep,
) -> FunctionActionResponse:
    """Create a new function in a pod."""
    user: UserEntity = request.state.user
    entity = FunctionEntity(
        pod_id=pod_id,
        user_id=user.id,
        name=normalize_resource_name(data.name),
        description=data.description,
        icon_url=data.icon_url,
        config=data.config,
        type=data.type,
        visibility=data.visibility.value,
    )
    function = await use_cases.create_function(
        pod_id=pod_id, entity=entity, user_id=user.id, code=data.code, request=request
    )
    return await _function_action_response(function)


@router.get(
    "",
    response_model=FunctionListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.list",
    summary="List Functions",
    description="List all functions in a pod",
    dependencies=[FunctionViewerDep],
)
async def list_functions(
    request: Request,
    pod_id: UUID,
    function_service: FunctionServiceDep,
    ctx: PodContextDep,
    limit: int = Query(default=100, ge=1, le=1000),
    page_token: Optional[str] = Query(default=None),
) -> FunctionListResponse:
    """List all functions in a pod."""
    user: UserEntity = request.state.user
    user_id = user.id

    parse_uuid_page_token(page_token)

    functions, next_cursor = await function_service.list_functions(
        pod_id, user_id, limit, page_token, ctx=ctx
    )

    return FunctionListResponse(
        items=[_function_summary_response(f) for f in functions],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/{function_name}",
    response_model=FunctionDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.get",
    summary="Get Function",
    description="Get a function by name",
    dependencies=[FunctionResourceViewerDep],
)
async def get_function(
    request: Request,
    pod_id: UUID,
    function_name: str,
    function_service: FunctionServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> FunctionDetailResponse:
    """Get a function by name."""
    user: UserEntity = request.state.user
    user_id = user.id

    function = await function_service.get_function_by_name(
        pod_id, function_name, user_id, raise_not_found=True, ctx=ctx
    )

    assert function is not None
    response = await _function_action_response(function)
    return FunctionDetailResponse(
        **response.model_dump(),
        permissions=await _function_permissions_response(
            uow,
            pod_id=pod_id,
            function=function,
        ),
    )


@router.get(
    "/{function_name}/permissions",
    response_model=FunctionPermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.permissions.get",
    summary="Get Function Resource Permissions",
    description="Get explicit resource grants assigned to a function.",
    dependencies=[FunctionResourceViewerDep],
)
async def get_function_permissions(
    request: Request,
    pod_id: UUID,
    function_name: str,
    function_service: FunctionServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> FunctionPermissionsResponse:
    user: UserEntity = request.state.user
    function = await function_service.get_function_by_name(
        pod_id,
        function_name,
        user.id,
        raise_not_found=True,
        include_code=False,
        ctx=ctx,
    )
    assert function is not None
    return await _function_permissions_response(uow, pod_id=pod_id, function=function)


@router.put(
    "/{function_name}/permissions",
    response_model=FunctionPermissionsResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.permissions.replace",
    summary="Replace Function Resource Permissions",
    description="Replace explicit resource grants assigned to a function.",
    dependencies=[FunctionResourceAdminDep],
)
async def replace_function_permissions(
    request: Request,
    pod_id: UUID,
    function_name: str,
    data: FunctionPermissionsReplaceRequest,
    function_service: FunctionServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
) -> FunctionPermissionsResponse:
    user: UserEntity = request.state.user
    function = await function_service.get_function_by_name(
        pod_id,
        function_name,
        user.id,
        raise_not_found=True,
        include_code=False,
        ctx=ctx,
    )
    assert function is not None
    assert function.id is not None
    validate_pod_resource_grant_permissions(data.grants)
    grants = await normalize_pod_resource_grants(
        uow.session,
        pod_id=pod_id,
        grants=data.grants,
    )
    await replace_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="FUNCTION",
        grantee_id=function.id,
        grants=grants,
        created_by_user_id=user.id,
    )
    await invalidate_function_workspace_env_cache(
        pod_id=pod_id,
        function_id=function.id,
    )
    return await _function_permissions_response(uow, pod_id=pod_id, function=function)


@router.patch(
    "/{function_name}",
    response_model=FunctionActionResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.update",
    summary="Update Function",
    description=(
        "Update a function. When code is supplied, the platform re-derives the "
        "function input_schema and output_schema and returns the refreshed function."
    ),
    dependencies=[FunctionResourceEditorDep],
)
async def update_function(
    request: Request,
    pod_id: UUID,
    function_name: str,
    data: UpdateFunctionRequest,
    use_cases: FunctionUseCasesDep,
) -> FunctionActionResponse:
    """Update a function."""
    user: UserEntity = request.state.user
    update_entity = FunctionUpdateEntity(**data.model_dump(exclude_unset=True))
    function = await use_cases.update_function(
        pod_id=pod_id,
        name=function_name,
        update_entity=update_entity,
        user_id=user.id,
        request=request,
    )
    return await _function_action_response(function)


@router.delete(
    "/{function_name}",
    response_model=FunctionMessageResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.delete",
    summary="Delete Function",
    description="Delete a function",
    dependencies=[FunctionResourceDeleteDep],
)
async def delete_function(
    request: Request,
    pod_id: UUID,
    function_name: str,
    use_cases: FunctionUseCasesDep,
) -> FunctionMessageResponse:
    """Delete a function."""
    user: UserEntity = request.state.user
    await use_cases.delete_function(
        pod_id=pod_id, name=function_name, user_id=user.id, request=request
    )
    return FunctionMessageResponse(
        message=f"Function {function_name} deleted successfully"
    )


@router.post(
    "/{function_name}/runs",
    response_model=FunctionRunResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.run",
    summary="Execute Function",
    description="Execute a function",
    # No route-level FunctionResourceExecuteDep: that request-scoped dependency
    # pins its pooled DB connection for the whole request, including the slow
    # sandbox execution. Authorization happens instead inside the use-case's
    # short pod_context_scope (function_service.resolve_execute ->
    # ctx.require(FUNCTION_EXECUTE)), so the connection is released before the
    # sandbox round-trip. Mirrors conversation_controller.send_message.
)
async def execute_function(
    request: Request,
    pod_id: UUID,
    function_name: str,
    data: ExecuteFunctionRequest,
    use_cases: FunctionUseCasesDep,
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FunctionRunResponse:
    """Execute a function."""
    user: UserEntity = request.state.user
    user_id = user.id
    user_email = getattr(user, "email", None)
    if user_email is None:
        async with uow_factory() as uow:
            resolved_user = await UserRepository(uow).get(user_id)
        user_email = str(resolved_user.email) if resolved_user is not None else None

    run = await use_cases.execute_function(
        pod_id=pod_id,
        name=function_name,
        input_data=data.input_data,
        user_id=user_id,
        user_email=user_email,
        request=request,
    )
    return FunctionRunResponse.model_validate(run)


@router.get(
    "/{function_name}/runs",
    response_model=FunctionRunListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.run.list",
    summary="List Runs",
    description="List runs for a function",
    dependencies=[FunctionResourceViewerDep],
)
async def list_runs(
    request: Request,
    pod_id: UUID,
    function_name: str,
    function_service: FunctionServiceDep,
    ctx: PodContextDep,
    limit: int = Query(default=100, ge=1, le=1000),
    page_token: Optional[str] = Query(default=None),
) -> FunctionRunListResponse:
    """List runs for a function."""
    user: UserEntity = request.state.user
    user_id = user.id

    parse_uuid_page_token(page_token)

    runs, next_cursor = await function_service.list_runs(
        pod_id, function_name, user_id, limit, page_token, ctx=ctx
    )

    return FunctionRunListResponse(
        items=[FunctionRunSummaryResponse.model_validate(r) for r in runs],
        limit=limit,
        next_page_token=next_cursor,
    )


async def _function_permissions_response(
    uow: UoWDep,
    *,
    pod_id: UUID,
    function: FunctionEntity,
) -> FunctionPermissionsResponse:
    assert function.id is not None
    grouped = await list_grantee_resource_grants(
        uow.session,
        pod_id=pod_id,
        grantee_type="FUNCTION",
        grantee_id=function.id,
    )
    return FunctionPermissionsResponse(
        function_id=function.id,
        function_name=function.name,
        grants=[
            FunctionResourcePermissionResponse(
                resource_type=resource_type,
                resource_name=resource_name,
                permission_ids=sorted(set(permission_ids)),
            )
            for (resource_type, resource_name), permission_ids in grouped.items()
        ],
    )


@router.get(
    "/{function_name}/runs/{run_id}",
    response_model=FunctionRunResponse,
    status_code=status.HTTP_200_OK,
    operation_id="function.run.get",
    summary="Get Run",
    description="Get a specific function run",
    dependencies=[FunctionResourceViewerDep],
)
async def get_run(
    request: Request,
    pod_id: UUID,
    function_name: str,
    run_id: UUID,
    function_service: FunctionServiceDep,
    ctx: PodContextDep,
) -> FunctionRunResponse:
    """Get a specific function run."""
    user: UserEntity = request.state.user
    user_id = user.id

    run = await function_service.get_run(pod_id, function_name, run_id, user_id, ctx=ctx)

    return FunctionRunResponse.model_validate(run)

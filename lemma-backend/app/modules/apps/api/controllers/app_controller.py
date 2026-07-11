"""App API controller."""

from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse

from app.core.api.dependencies import CurrentUser
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.dependencies import PodContextDep
from app.core.helpers.slug import normalize_resource_name
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import (
    AppServiceDep,
    AppUseCasesDep,
    WidgetContentReaderDep,
)
from app.modules.apps.api.schemas.app_schemas import (
    CreateAppFromWidgetRequest,
    CreateAppRequest,
    AppBundleUploadResponse,
    AppDetailResponse,
    AppListResponse,
    AppMessageResponse,
    UpdateAppRequest,
)
from app.modules.apps.domain.entities import (
    AppEntity,
    AppUpdateEntity,
)

router = APIRouter(
    prefix="/pods/{pod_id}/apps",
    tags=["Apps"],
    redirect_slashes=False,
)

ZIP_FILE_RESPONSE = {
    200: {
        "description": "Zip archive bytes",
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}


async def _app_detail_response(ctx: PodContextDep, app: AppEntity) -> AppDetailResponse:
    _ = ctx
    return AppDetailResponse.model_validate(app)


@router.post(
    "",
    response_model=AppDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="app.create",
    summary="Create App",
)
async def create_app(
    pod_id: UUID,
    data: CreateAppRequest,
    app_service: AppServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> AppDetailResponse:
    entity_data = {
        "pod_id": pod_id,
        "user_id": user.id,
        "name": normalize_resource_name(data.name),
        "public_slug": data.public_slug or data.name,
        "description": data.description,
    }
    if data.visibility is not None:
        entity_data["visibility"] = data.visibility
    entity = AppEntity(**entity_data)
    app = await app_service.create_app_with_context(entity, user.id, ctx=ctx)
    return await _app_detail_response(ctx, app)


@router.post(
    "/from-widget",
    response_model=AppDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="app.create_from_widget",
    summary="Save Widget As App",
)
async def create_app_from_widget(
    pod_id: UUID,
    data: CreateAppFromWidgetRequest,
    app_service: AppServiceDep,
    reader: WidgetContentReaderDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> AppDetailResponse:
    """Promote a conversation widget into a persisted app.

    The widget and the app share one source artifact at two lifecycle stages. This
    fetches the stored fragment, preserves it, and deploys it in the standalone
    document wrapper (without conversation-only padding or height messaging).
    """
    artifact = await reader.get_widget(data.conversation_id, data.tool_call_id)
    if artifact is None or artifact.pod_id != pod_id:
        raise HTTPException(status_code=404, detail="Widget not found")

    app = await app_service.create_app_from_widget(
        pod_id,
        user.id,
        artifact=artifact,
        name=data.name,
        public_slug=data.public_slug,
        description=data.description,
        visibility=data.visibility,
        ctx=ctx,
    )
    return await _app_detail_response(ctx, app)


@router.get(
    "",
    response_model=AppListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.list",
    summary="List Apps",
)
async def list_apps(
    pod_id: UUID,
    app_service: AppServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    limit: int = Query(default=100, ge=1, le=1000),
    page_token: Optional[str] = Query(default=None),
) -> AppListResponse:
    parse_uuid_page_token(page_token)

    apps, next_cursor = await app_service.list_apps(
        pod_id,
        user.id,
        limit,
        page_token,
        ctx=ctx,
    )
    return AppListResponse(
        items=[AppDetailResponse.model_validate(app) for app in apps],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/{app_name}",
    response_model=AppDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.get",
    summary="Get App",
)
async def get_app(
    pod_id: UUID,
    app_name: str,
    app_service: AppServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> AppDetailResponse:
    app = await app_service.get_app_by_name(
        pod_id,
        app_name,
        user.id,
        raise_not_found=True,
        ctx=ctx,
    )
    return await _app_detail_response(ctx, app)


@router.patch(
    "/{app_name}",
    response_model=AppDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.update",
    summary="Update App",
)
async def update_app(
    pod_id: UUID,
    app_name: str,
    data: UpdateAppRequest,
    app_service: AppServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> AppDetailResponse:
    app = await app_service.update_app(
        pod_id,
        app_name,
        AppUpdateEntity(
            description=data.description,
            public_slug=data.public_slug,
            visibility=data.visibility,
        ),
        user.id,
        ctx=ctx,
    )
    return await _app_detail_response(ctx, app)


@router.delete(
    "/{app_name}",
    response_model=AppMessageResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.delete",
    summary="Delete App",
)
async def delete_app(
    pod_id: UUID,
    app_name: str,
    user: CurrentUser,
    request: Request,
    use_cases: AppUseCasesDep,
) -> AppMessageResponse:
    await use_cases.delete_app(
        pod_id=pod_id, app_name=app_name, request=request, user_id=user.id
    )
    return AppMessageResponse(message=f"App {app_name} deleted successfully")


@router.post(
    "/{app_name}/bundle",
    response_model=AppBundleUploadResponse,
    status_code=status.HTTP_200_OK,
    operation_id="app.bundle.upload",
    summary="Upload App Bundle",
)
async def upload_app_bundle(
    request: Request,
    pod_id: UUID,
    app_name: str,
    user: CurrentUser,
    use_cases: AppUseCasesDep,
    source_archive: UploadFile | None = File(default=None),
    dist_archive: UploadFile | None = File(default=None),
) -> AppBundleUploadResponse:
    source_archive_bytes: bytes | None = None
    dist_archive_bytes: bytes | None = None
    if source_archive is not None:
        source_archive_bytes = await source_archive.read()
    if dist_archive is not None:
        dist_archive_bytes = await dist_archive.read()

    app = await use_cases.upload_bundle(
        pod_id=pod_id,
        app_name=app_name,
        request=request,
        user_id=user.id,
        source_archive_bytes=source_archive_bytes,
        dist_archive_bytes=dist_archive_bytes,
    )
    return AppBundleUploadResponse(
        message="Bundle uploaded successfully",
        app=AppDetailResponse.model_validate(app),
    )


@router.get(
    "/{app_name}/assets",
    status_code=status.HTTP_200_OK,
    operation_id="app.asset.root.get",
    summary="Get App Root Asset",
)
async def get_app_root_asset(
    request: Request,
    pod_id: UUID,
    app_name: str,
    user: CurrentUser,
    use_cases: AppUseCasesDep,
) -> Response:
    asset = await use_cases.serve_asset(
        pod_id=pod_id,
        app_name=app_name,
        request=request,
        user_id=user.id,
        asset_path=None,
        request_etag=request.headers.get("if-none-match"),
    )
    return app_asset_response(asset)


@router.get(
    "/{app_name}/assets/{asset_path:path}",
    status_code=status.HTTP_200_OK,
    operation_id="app.asset.get",
    summary="Get App Asset",
)
async def get_app_asset(
    request: Request,
    pod_id: UUID,
    app_name: str,
    asset_path: str,
    user: CurrentUser,
    use_cases: AppUseCasesDep,
) -> Response:
    asset = await use_cases.serve_asset(
        pod_id=pod_id,
        app_name=app_name,
        request=request,
        user_id=user.id,
        asset_path=asset_path,
        request_etag=request.headers.get("if-none-match"),
    )
    return app_asset_response(asset)


@router.get(
    "/{app_name}/source/archive",
    status_code=status.HTTP_200_OK,
    operation_id="app.source.archive.get",
    summary="Download App Source Archive",
    response_class=StreamingResponse,
    responses=ZIP_FILE_RESPONSE,
)
async def download_app_source_archive(
    pod_id: UUID,
    app_name: str,
    user: CurrentUser,
    request: Request,
    use_cases: AppUseCasesDep,
):
    archive = await use_cases.download_source_archive(
        pod_id=pod_id, app_name=app_name, request=request, user_id=user.id
    )
    return StreamingResponse(
        BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={app_name}-source.zip"},
    )


@router.get(
    "/{app_name}/dist/archive",
    status_code=status.HTTP_200_OK,
    operation_id="app.dist.archive.get",
    summary="Download App Dist Archive",
    response_class=StreamingResponse,
    responses=ZIP_FILE_RESPONSE,
)
async def download_app_dist_archive(
    pod_id: UUID,
    app_name: str,
    user: CurrentUser,
    request: Request,
    use_cases: AppUseCasesDep,
):
    archive = await use_cases.download_dist_archive(
        pod_id=pod_id, app_name=app_name, request=request, user_id=user.id
    )
    return StreamingResponse(
        BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={app_name}-dist.zip"},
    )

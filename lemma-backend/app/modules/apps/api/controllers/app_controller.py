"""App API controller."""

from io import BytesIO
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.pagination import parse_uuid_page_token
from app.core.authorization.current import (
    reset_current_context,
    set_current_context,
)
from app.core.authorization.dependencies import PodContextDep, resolve_pod_context
from app.core.helpers.slug import normalize_resource_name
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.apps.api.asset_response import app_asset_response
from app.modules.apps.api.dependencies import (
    AppServiceDep,
    WidgetContentReaderDep,
    build_app_service,
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
    AppAssetDocument,
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

    The widget and the app are the same artifact at two lifecycle stages: this
    fetches the widget's stored HTML and deploys it as the app's bundle —
    identical to what was shown.
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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> AppMessageResponse:
    # Delete the row in a short UoW, then clean up the app's stored bytes with no
    # pooled connection held (the cleanup can touch many objects).
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            cleanup = await app_service.resolve_delete_app(
                pod_id, app_name, user.id, ctx=ctx
            )
        finally:
            reset_current_context(token)
    await app_service.cleanup_app_storage(cleanup)
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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
    source_archive: UploadFile | None = File(default=None),
    dist_archive: UploadFile | None = File(default=None),
) -> AppBundleUploadResponse:
    source_archive_bytes: bytes | None = None
    dist_archive_bytes: bytes | None = None
    if source_archive is not None:
        source_archive_bytes = await source_archive.read()
    if dist_archive is not None:
        dist_archive_bytes = await dist_archive.read()

    # Resolve+authorize+dedup in a short UoW, write the bundle bytes with no
    # pooled connection held, then persist the release pointer in a second UoW.
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            plan = await app_service.resolve_upload_bundle(
                pod_id,
                app_name,
                user.id,
                has_source=source_archive_bytes is not None,
                dist_archive_bytes=dist_archive_bytes,
                ctx=ctx,
            )
        finally:
            reset_current_context(token)
    written = await app_service.write_bundle_storage(
        plan, source_archive_bytes, dist_archive_bytes
    )
    async with uow_factory() as uow2:
        app = await build_app_service(uow2).finalize_upload_bundle(
            plan, written, user.id
        )
    return AppBundleUploadResponse(
        message="Bundle uploaded successfully",
        app=AppDetailResponse.model_validate(app),
    )


async def _serve_app_asset(
    *,
    request: Request,
    pod_id: UUID,
    app_name: str,
    user_id: UUID,
    asset_path: str | None,
    uow_factory: UnitOfWorkFactory,
) -> Response:
    # Resolve + authorize + ETag in a short UoW (connection released here), then
    # read the asset bytes from storage with no pooled connection held — a
    # request-scoped AppServiceDep/PodContextDep would pin the connection for the
    # whole response while reading from GCS/local storage.
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user_id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            resolved = await app_service.resolve_app_asset(
                pod_id,
                app_name,
                user_id,
                asset_path=asset_path,
                request_etag=request.headers.get("if-none-match"),
                ctx=ctx,
            )
        finally:
            reset_current_context(token)
    if isinstance(resolved, AppAssetDocument):
        return app_asset_response(resolved)
    asset = await app_service.read_app_asset(resolved)
    return app_asset_response(asset)


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    return await _serve_app_asset(
        request=request,
        pod_id=pod_id,
        app_name=app_name,
        user_id=user.id,
        asset_path=None,
        uow_factory=uow_factory,
    )


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    return await _serve_app_asset(
        request=request,
        pod_id=pod_id,
        app_name=app_name,
        user_id=user.id,
        asset_path=asset_path,
        uow_factory=uow_factory,
    )


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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
):
    # Resolve + authorize in a short UoW (auth context built there, not via a
    # request-scoped PodContextDep held across the stream), then read the archive
    # from storage and stream it without holding a pooled connection.
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            app_id, archive_path = await app_service.resolve_source_archive(
                pod_id, app_name, user.id, ctx=ctx
            )
        finally:
            reset_current_context(token)
    archive = await app_service.read_archive(app_id, archive_path)

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
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
):
    # Resolve + authorize in a short UoW (auth context built there, not via a
    # request-scoped PodContextDep held across the stream), then read the archive
    # from storage and stream it without holding a pooled connection.
    async with uow_factory() as uow:
        app_service = build_app_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            app_id, archive_path = await app_service.resolve_dist_archive(
                pod_id, app_name, user.id, ctx=ctx
            )
        finally:
            reset_current_context(token)
    archive = await app_service.read_archive(app_id, archive_path)

    return StreamingResponse(
        BytesIO(archive),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={app_name}-dist.zip"},
    )

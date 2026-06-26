from __future__ import annotations

import unicodedata
from io import BytesIO
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse

from app.core.api.pagination import parse_uuid_page_token
from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.config import settings
from app.core.authorization.current import (
    reset_current_context,
    set_current_context,
)
from app.core.authorization.dependencies import PodContextDep, resolve_pod_context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.datastore.api.dependencies import FileServiceDep, build_file_service
from app.modules.datastore.infrastructure.reindex_queue import (
    enqueue_datastore_path_cleanup,
)
from app.core.log.log import get_logger
from app.modules.datastore.api.schemas.datastore_schemas import (
    CreateFolderRequest,
    DirectoryTreeResponse,
    FileChildrenResponse,
    FileChildSchema,
    FileDetailResponse,
    FileListResponse,
    FileResponse,
    FileSummaryResponse,
    FileSearchRequest,
    FileSearchResponse,
    FileSearchResultSchema,
    FileSignedUrlRequest,
    FileSignedUrlResponse,
    FileUrlResponse,
)
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import DatastoreFileUpdateEntity
from app.modules.datastore.services.files.file_url import build_file_app_url


def build_content_disposition(disposition_type: str, filename: str) -> str:
    """Build a Content-Disposition header value with an ASCII fallback and a
    UTF-8 ``filename*`` for non-ASCII names."""
    normalized_ascii = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ascii_filename = (normalized_ascii or "download").replace("\\", "_").replace('"', "_")
    encoded_filename = quote(filename, safe="")
    return (
        f'{disposition_type}; filename="{ascii_filename}"; '
        f"filename*=UTF-8''{encoded_filename}"
    )

router = APIRouter(
    prefix="/pods/{pod_id}/datastore/files",
    tags=["files"],
    redirect_slashes=False,
)

logger = get_logger(__name__)

BINARY_FILE_RESPONSE = {
    200: {
        "description": "File bytes",
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            }
        },
    }
}

def _ensure_file_in_pod(file_entity: FileResponse, pod_id: UUID) -> None:
    if file_entity.pod_id != pod_id:
        raise DatastoreValidationError("File does not belong to this pod")


def _to_file_response(file_entity, current_user_id: UUID) -> FileResponse:
    response = FileResponse.model_validate(file_entity)
    response.path = _to_public_file_path(
        file_entity.path,
        current_user_id=current_user_id,
        owner_user_id=file_entity.owner_user_id,
    )
    return response


async def _file_detail_response(
    file_entity,
    current_user_id: UUID,
) -> FileDetailResponse:
    file_response = _to_file_response(file_entity, current_user_id)
    return FileDetailResponse(
        **file_response.model_dump(),
        allowed_actions=file_entity.allowed_actions,
    )


def _to_public_file_path(
    path: str,
    *,
    current_user_id: UUID,
    owner_user_id: UUID | None,
) -> str:
    if owner_user_id == current_user_id:
        personal_root = f"/{current_user_id}"
        if path == personal_root:
            return "/me"
        if path.startswith(f"{personal_root}/"):
            return f"/me{path.removeprefix(personal_root)}"
    return path


def _to_public_tree_paths(node: dict, *, current_user_id: UUID) -> dict:
    public_node = dict(node)
    personal_root = f"/{current_user_id}"
    if public_node["path"] == personal_root:
        public_node["path"] = "/me"
    elif public_node["path"].startswith(f"{personal_root}/"):
        public_node["path"] = f"/me{public_node['path'].removeprefix(personal_root)}"
    public_node["children"] = [
        _to_public_tree_paths(
            child,
            current_user_id=current_user_id,
        )
        for child in public_node.get("children", [])
    ]
    return public_node


@router.post(
    "",
    response_model=FileDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="file.upload",
    summary="Upload File",
)
async def upload_file(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    data: UploadFile = File(...),
    name: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    directory_path: str = Form("/"),
    search_enabled: bool = Form(True),
    visibility: str | None = Form(default=None),
) -> FileDetailResponse:
    file_content = await data.read()
    file_name = name or data.filename or "untitled"

    file_entity = await file_service.create_file(
        pod_id=pod_id,
        name=file_name,
        file_content=file_content,
        ctx=ctx,
        description=description,
        directory_path=directory_path,
        search_enabled=search_enabled,
        visibility=visibility,
    )
    return await _file_detail_response(file_entity, user.id)


@router.post(
    "/folders",
    response_model=FileDetailResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="file.folder.create",
    summary="Create Folder",
)
async def create_folder(
    pod_id: UUID,
    data: CreateFolderRequest,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
) -> FileDetailResponse:
    path = data.path
    if path is None:
        if not data.name:
            raise DatastoreValidationError("Either path or name is required")
        parent_path = "/"
        if data.parent_id is not None:
            parent = await file_service.get_file(data.parent_id, ctx=ctx)
            parent_path = parent.path
        path = f"/{data.name}" if parent_path == "/" else f"{parent_path}/{data.name}"

    folder = await file_service.create_folder(
        pod_id=pod_id,
        path=path,
        ctx=ctx,
        description=data.description,
        visibility=data.visibility,
    )
    return await _file_detail_response(folder, user.id)


@router.get(
    "",
    response_model=FileListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.list",
    summary="List Files",
)
async def list_files(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    directory_path: str = Query(default="/"),
    limit: int = Query(default=100, ge=1, le=1000),
    page_token: Optional[str] = Query(default=None),
) -> FileListResponse:
    try:
        parse_uuid_page_token(page_token)
    except ValueError as exc:
        raise DatastoreValidationError("Invalid page_token") from exc

    items, next_cursor = await file_service.list_files(
        pod_id=pod_id,
        ctx=ctx,
        directory_path=directory_path,
        limit=limit,
        cursor=page_token,
    )

    summary_fields = set(FileSummaryResponse.model_fields) - {"allowed_actions"}
    return FileListResponse(
        items=[
            FileSummaryResponse(
                **_to_file_response(item, user.id).model_dump(include=summary_fields),
                allowed_actions=item.allowed_actions,
            )
            for item in items
        ],
        limit=limit,
        next_page_token=next_cursor,
    )


@router.get(
    "/by-path",
    response_model=FileDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.get",
    summary="Get File",
)
async def get_file(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    path: str = Query(...),
) -> FileDetailResponse:
    file_entity = await file_service.get_file_by_path(
        pod_id,
        path,
        ctx=ctx,
    )
    response = await _file_detail_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)
    return response


@router.patch(
    "/by-path",
    response_model=FileDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.update",
    summary="Update File",
)
async def update_file(
    pod_id: UUID,
    request: Request,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    data: UploadFile | None = File(default=None),
    path: str = Form(...),
    new_path: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    search_enabled: Optional[bool] = Form(None),
    visibility: str | None = Form(default=None),
) -> FileDetailResponse:
    form = await request.form()
    provided_fields = set(form.keys())
    file_content = await data.read() if data is not None else None

    update_payload: dict[str, object | None] = {}
    update_payload["path"] = path
    if "visibility" in provided_fields:
        update_payload["visibility"] = visibility
    if "new_path" in provided_fields:
        update_payload["new_path"] = new_path
    if "description" in provided_fields:
        update_payload["description"] = description
    if "search_enabled" in provided_fields:
        update_payload["search_enabled"] = search_enabled
    if data is not None:
        update_payload["content"] = file_content

    update_entity = DatastoreFileUpdateEntity(**update_payload)
    file_entity = await file_service.update_file_by_path(pod_id, update_entity, ctx=ctx)
    file_entity = await file_service.get_file(file_entity.id, ctx=ctx)
    response = await _file_detail_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)
    return response


@router.delete(
    "/by-path",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="file.delete",
    summary="Delete File Or Folder",
)
async def delete_path(
    pod_id: UUID,
    user: CurrentUser,
    request: Request,
    path: str = Query(...),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> Response:
    # Authorize + delete the rows in a short UoW (released on exit), then offload
    # the storage + search-index cleanup to the worker so the API never holds a
    # pooled connection across the (potentially many-object) purge. If the
    # enqueue fails, clean up in-process (still no connection held) so deleted
    # rows never leave orphaned blobs.
    async with uow_factory() as uow:
        file_service = build_file_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            cleanup = await file_service.resolve_delete_path(pod_id, path, ctx=ctx)
        finally:
            reset_current_context(token)

    files = list(cleanup.files)
    enqueued = False
    if not settings.e2e_disable_worker_file_autoindex:
        # When the worker file-path is active, offload the purge; otherwise (e2e
        # without a datastore worker) fall through to in-process cleanup below.
        try:
            enqueued = await enqueue_datastore_path_cleanup(
                pod_id=cleanup.pod_id,
                is_folder=cleanup.is_folder,
                folder_prefix=cleanup.folder_prefix,
                files=files,
            )
        except Exception as exc:
            logger.warning("Failed to enqueue datastore path cleanup: %s", exc)
            enqueued = False
    if not enqueued:
        await file_service.cleanup_deleted_paths(
            cleanup.pod_id,
            is_folder=cleanup.is_folder,
            folder_prefix=cleanup.folder_prefix,
            files=files,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/download",
    operation_id="file.download",
    summary="Download File",
    response_class=StreamingResponse,
    responses=BINARY_FILE_RESPONSE,
)
async def download_file(
    pod_id: UUID,
    user: CurrentUser,
    request: Request,
    path: str = Query(...),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    # Build the auth context inside the short UoW (not via a request-scoped
    # PodContextDep, which FastAPI keeps alive for the whole StreamingResponse),
    # resolve+authorize, release the connection, then read bytes from storage.
    async with uow_factory() as uow:
        file_service = build_file_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            file_entity = await file_service.resolve_readable_file(pod_id, path, ctx=ctx)
        finally:
            reset_current_context(token)
    content = await file_service.read_file_content(file_entity)
    response = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)

    content_type = file_entity.content_type
    disposition_type = (
        "inline"
        if (
            content_type.startswith("application/pdf")
            or content_type.startswith("image/")
            or content_type.startswith("text/")
        )
        else "attachment"
    )
    headers = {
        "Content-Disposition": build_content_disposition(
            disposition_type,
            file_entity.name,
        )
    }

    return StreamingResponse(
        BytesIO(content),
        media_type=content_type,
        headers=headers,
    )


@router.get(
    "/children",
    response_model=FileChildrenResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.children.list",
    summary="List a document's derived child files",
)
async def list_file_children(
    pod_id: UUID,
    user: CurrentUser,
    request: Request,
    path: str = Query(...),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> FileChildrenResponse:
    # Resolve + authorize inside a short UoW (released on exit), then read the
    # child manifest from storage with no pooled DB connection held.
    async with uow_factory() as uow:
        file_service = build_file_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            file_entity = await file_service.resolve_children_file(
                pod_id, path, ctx=ctx
            )
        finally:
            reset_current_context(token)
    children = await file_service.load_file_children(file_entity, user.id)
    response = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)
    return FileChildrenResponse(
        path=response.path,
        items=[FileChildSchema.model_validate(child) for child in children],
    )


@router.get(
    "/url",
    response_model=FileUrlResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.url",
    summary="Get a short-lived URL for a file",
)
async def get_file_url(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    path: str = Query(...),
) -> FileUrlResponse:
    file_entity, url, expires_at = await file_service.get_file_url(
        pod_id,
        path,
        ctx=ctx,
    )
    public = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(public, pod_id)
    return FileUrlResponse(
        path=public.path,
        url=url,
        app_url=build_file_app_url(pod_id, public.path),
        expires_at=expires_at,
    )


@router.post(
    "/signed-url",
    response_model=FileSignedUrlResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="file.signed_url",
    summary="Create a public, hit-capped signed URL for a file",
)
async def create_file_signed_url(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    path: str = Query(...),
    body: FileSignedUrlRequest | None = None,
) -> FileSignedUrlResponse:
    body = body or FileSignedUrlRequest()
    file_entity, signed_url, expires_at, max_hits = await file_service.create_signed_url(
        pod_id,
        path,
        ctx=ctx,
        expires_seconds=body.expires_seconds,
        max_hits=body.max_hits,
    )
    public = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(public, pod_id)
    return FileSignedUrlResponse(
        path=public.path,
        signed_url=signed_url,
        expires_at=expires_at,
        max_hits=max_hits,
    )


@router.get(
    "/children/content",
    operation_id="file.child.get",
    summary="Fetch a document's child artifact by path",
    response_class=StreamingResponse,
    responses=BINARY_FILE_RESPONSE,
)
async def download_file_child(
    pod_id: UUID,
    user: CurrentUser,
    request: Request,
    path: str = Query(
        ...,
        description="Child path, e.g. /folder/report.pdf/document.md, "
        "/folder/report.pdf/image_0.png, or /folder/report.pdf/pages/page_0001.jpg",
    ),
    page_start: Optional[int] = Query(default=None, ge=1),
    page_end: Optional[int] = Query(default=None, ge=1),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> StreamingResponse:
    # Build the auth context inside the short UoW (not via a request-scoped
    # PodContextDep held across the StreamingResponse), resolve+authorize, release
    # the connection, then render/read the artifact and stream it.
    async with uow_factory() as uow:
        file_service = build_file_service(uow)
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user.id, pod_id=pod_id
        )
        token = set_current_context(ctx)
        try:
            file_entity, artifact_rel = await file_service.resolve_child(
                pod_id, path, ctx=ctx
            )
        finally:
            reset_current_context(token)
    artifact_name, content, content_type = await file_service.read_child_content(
        file_entity,
        artifact_rel,
        page_start=page_start,
        page_end=page_end,
    )
    response = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)

    disposition_type = (
        "inline"
        if content_type.startswith(("text/", "image/", "application/json"))
        else "attachment"
    )
    download_name = artifact_name.rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": build_content_disposition(
            disposition_type,
            download_name,
        )
    }
    return StreamingResponse(
        BytesIO(content),
        media_type=content_type,
        headers=headers,
    )


@router.post(
    "/search",
    response_model=FileSearchResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.search",
    summary="Search Files",
)
async def search_files(
    pod_id: UUID,
    data: FileSearchRequest,
    file_service: FileServiceDep,
    ctx: PodContextDep,
) -> FileSearchResponse:
    results = await file_service.search_files(
        pod_id=pod_id,
        query=data.query,
        ctx=ctx,
        limit=data.limit,
        search_method=data.search_method,
        scope_path=data.scope_path,
        include_descendants=data.scope_mode.value == "SUBTREE",
    )

    return FileSearchResponse(
        items=[FileSearchResultSchema.model_validate(item) for item in results],
        total=len(results),
        query=data.query,
        search_method=data.search_method,
    )


@router.get(
    "/tree",
    response_model=DirectoryTreeResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.tree",
    summary="Get Directory Tree",
)
async def get_directory_tree(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    root_path: str = Query(default="/"),
    files_per_directory: int = Query(default=3, ge=0, le=20),
) -> DirectoryTreeResponse:
    tree = await file_service.get_directory_tree(
        pod_id=pod_id,
        ctx=ctx,
        root_path=root_path,
        files_per_directory=files_per_directory,
    )
    public_tree = _to_public_tree_paths(
        tree,
        current_user_id=user.id,
    )
    return DirectoryTreeResponse(
        root_path=public_tree["path"],
        files_per_directory=files_per_directory,
        tree=public_tree,
    )

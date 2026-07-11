from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import StreamingResponse

from app.core.api.pagination import parse_uuid_page_token
from app.core.api.streaming_multipart import (
    MultipartFileLimit,
    stream_multipart_form,
)
from app.core.api.dependencies import CurrentUser
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.api.file_upload_openapi import (
    BINARY_FILE_RESPONSE,
    FILE_UPDATE_OPENAPI,
    FILE_UPLOAD_OPENAPI,
    MARKDOWN_ATTACH_OPENAPI,
)
from app.core.authorization.dependencies import PodContextDep
from app.modules.datastore.api.dependencies import FileServiceDep, FileUseCasesDep
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
from app.modules.datastore.api.file_download_response import (
    build_child_download_response,
    build_content_disposition as build_content_disposition,
    build_original_download_response,
)

router = APIRouter(
    prefix="/pods/{pod_id}/datastore/files",
    tags=["files"],
    redirect_slashes=False,
)


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
    openapi_extra=FILE_UPLOAD_OPENAPI,
)
async def upload_file(
    pod_id: UUID,
    request: Request,
    use_cases: FileUseCasesDep,
    user: CurrentUser,
) -> FileDetailResponse:
    async with stream_multipart_form(
        request,
        file_limits={
            "data": MultipartFileLimit(
                max_bytes=datastore_settings.datastore_upload_max_bytes,
                required=True,
                label="file",
            )
        },
        text_fields={
            "name",
            "description",
            "directory_path",
            "search_enabled",
            "visibility",
        },
        combined_max_bytes=datastore_settings.datastore_upload_max_bytes,
    ) as form:
        data = form.require_file("data")
        file_name = form.text("name") or data.filename or "untitled"
        file_entity = await use_cases.create_file(
            pod_id=pod_id,
            name=file_name,
            file_content=data.path,
            request=request,
            user_id=user.id,
            description=form.text("description"),
            directory_path=form.text("directory_path", "/") or "/",
            search_enabled=bool(form.boolean("search_enabled", True)),
            visibility=form.text("visibility"),
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
    openapi_extra=FILE_UPDATE_OPENAPI,
)
async def update_file(
    pod_id: UUID,
    request: Request,
    user: CurrentUser,
    use_cases: FileUseCasesDep,
) -> FileDetailResponse:
    async with stream_multipart_form(
        request,
        file_limits={
            "data": MultipartFileLimit(
                max_bytes=datastore_settings.datastore_upload_max_bytes,
                label="file",
            )
        },
        text_fields={
            "path",
            "new_path",
            "description",
            "search_enabled",
            "visibility",
        },
        combined_max_bytes=datastore_settings.datastore_upload_max_bytes,
    ) as form:
        staged = form.file("data")
        update_payload: dict[str, object | None] = {}
        update_payload["path"] = form.require_text("path")
        if form.has("visibility"):
            update_payload["visibility"] = form.text("visibility")
        if form.has("new_path"):
            update_payload["new_path"] = form.text("new_path")
        if form.has("description"):
            update_payload["description"] = form.text("description")
        if form.has("search_enabled"):
            update_payload["search_enabled"] = form.boolean("search_enabled")
        if staged is not None:
            update_payload["content"] = staged.path

        update_entity = DatastoreFileUpdateEntity(**update_payload)
        file_entity = await use_cases.update_file(
            pod_id=pod_id,
            update_entity=update_entity,
            request=request,
            user_id=user.id,
        )
    response = await _file_detail_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)
    return response


@router.put(
    "/by-path/markdown",
    response_model=FileDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.markdown.attach",
    summary="Attach Document Markdown",
    openapi_extra=MARKDOWN_ATTACH_OPENAPI,
)
async def attach_document_markdown(
    pod_id: UUID,
    request: Request,
    use_cases: FileUseCasesDep,
    user: CurrentUser,
) -> FileDetailResponse:
    """Attach user-authored markdown and referenced images to a document.

    The source file remains unchanged; the markdown is indexed for agent use.
    """
    async with stream_multipart_form(
        request,
        file_limits={
            "data": MultipartFileLimit(
                max_bytes=datastore_settings.datastore_markdown_max_bytes,
                required=True,
                label="markdown",
            ),
            "images": MultipartFileLimit(
                max_bytes=datastore_settings.datastore_markdown_image_max_bytes,
                multiple=True,
                label="markdown image",
            ),
        },
        text_fields={"path"},
        combined_max_bytes=datastore_settings.datastore_markdown_batch_max_bytes,
    ) as form:
        markdown = form.require_file("data")
        image_files = [
            (image.filename or "image", image.path) for image in form.files("images")
        ]
        file_entity = await use_cases.attach_user_markdown(
            pod_id=pod_id,
            path=form.require_text("path"),
            markdown_content=markdown.path,
            images=image_files,
            request=request,
            user_id=user.id,
        )
    return await _file_detail_response(file_entity, user.id)


@router.delete(
    "/by-path/markdown",
    response_model=FileDetailResponse,
    status_code=status.HTTP_200_OK,
    operation_id="file.markdown.detach",
    summary="Detach Document Markdown",
)
async def detach_document_markdown(
    pod_id: UUID,
    file_service: FileServiceDep,
    user: CurrentUser,
    ctx: PodContextDep,
    path: str = Query(...),
) -> FileDetailResponse:
    """Remove a document's user-provided markdown so it reverts to extraction."""
    file_entity = await file_service.detach_user_markdown(
        pod_id=pod_id, path=path, ctx=ctx
    )
    return await _file_detail_response(file_entity, user.id)


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
    use_cases: FileUseCasesDep,
    path: str = Query(...),
) -> Response:
    await use_cases.delete_path(
        pod_id=pod_id, path=path, request=request, user_id=user.id
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
    use_cases: FileUseCasesDep,
    path: str = Query(...),
) -> Response:
    download = await use_cases.download_file(
        pod_id=pod_id,
        path=path,
        request=request,
        user_id=user.id,
        if_none_match=request.headers.get("if-none-match"),
    )
    file_entity = download.entity
    response = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)

    return build_original_download_response(file_entity, download)


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
    use_cases: FileUseCasesDep,
    path: str = Query(...),
) -> FileChildrenResponse:
    result = await use_cases.list_children(
        pod_id=pod_id, path=path, request=request, user_id=user.id
    )
    response = _to_file_response(result.entity, user.id)
    _ensure_file_in_pod(response, pod_id)
    return FileChildrenResponse(
        path=response.path,
        items=[FileChildSchema.model_validate(child) for child in result.children],
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
    (
        file_entity,
        signed_url,
        expires_at,
        max_hits,
    ) = await file_service.create_signed_url(
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
    use_cases: FileUseCasesDep,
    path: str = Query(
        ...,
        description="Child path, e.g. /folder/report.pdf/document.md, "
        "/folder/report.pdf/image_0.png, or /folder/report.pdf/pages/page_0001.jpg",
    ),
    page_start: Optional[int] = Query(default=None, ge=1),
    page_end: Optional[int] = Query(default=None, ge=1),
) -> Response:
    result = await use_cases.download_child(
        pod_id=pod_id,
        path=path,
        request=request,
        user_id=user.id,
        page_start=page_start,
        page_end=page_end,
    )
    file_entity = result.entity
    artifact_name = result.artifact_name
    content = result.content
    content_type = result.content_type
    response = _to_file_response(file_entity, user.id)
    _ensure_file_in_pod(response, pod_id)

    return build_child_download_response(
        request_if_none_match=request.headers.get("if-none-match"),
        artifact_name=artifact_name,
        content=content,
        content_type=content_type,
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

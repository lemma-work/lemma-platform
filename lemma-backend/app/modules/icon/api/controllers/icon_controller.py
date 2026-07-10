"""Controllers for icon upload and public retrieval."""

from __future__ import annotations

import mimetypes

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from app.core.api.dependencies import CurrentUser
from app.core.api.streaming_multipart import (
    MultipartFileLimit,
    stream_multipart_form,
    streaming_multipart_openapi,
)
from app.core.file_types import sniff_image_mime
from app.modules.icon.api.schemas import IconUploadResponse
from app.modules.icon.config import icon_settings
from app.modules.icon.services.icon_service import IconService
from app.modules.icon.services.raster_validation import validate_raster_icon

router = APIRouter(tags=["icons"])

# Icons are served inline from the API origin, so only inert raster formats are
# allowed. SVG is XML and can carry <script>, which would execute on our own
# (cookie-bearing) origin — it is rejected on upload and never served as active
# content. (security_appsec-03)
_SAFE_ICON_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
)


@router.post(
    "/icons/upload",
    response_model=IconUploadResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="icon.upload",
    summary="Upload Icon",
    description="Upload an image asset and receive a public icon URL.",
    openapi_extra=streaming_multipart_openapi(
        "IconUploadRequest",
        properties={
            "file": {
                "type": "string",
                "format": "binary",
                "contentMediaType": "application/octet-stream",
                "title": "File",
            }
        },
        required=["file"],
    ),
)
async def upload_icon(
    request: Request,
    user: CurrentUser,
) -> IconUploadResponse:
    async with stream_multipart_form(
        request,
        file_limits={
            "file": MultipartFileLimit(
                max_bytes=icon_settings.icon_upload_max_bytes,
                required=True,
                label="icon",
            )
        },
        combined_max_bytes=icon_settings.icon_upload_max_bytes,
    ) as form:
        file_content = await form.require_file("file").read_bytes()
    if not file_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded icon is empty",
        )

    # Validate the ACTUAL bytes via magic-byte sniffing, not the client-supplied
    # Content-Type/filename. This rejects SVG and any disguised/non-raster payload.
    sniffed_type = sniff_image_mime(file_content)
    if sniffed_type not in _SAFE_ICON_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PNG, JPEG, GIF, WEBP, or BMP icons are supported",
        )
    try:
        validate_raster_icon(
            file_content,
            detected_media_type=sniffed_type,
            max_dimension=icon_settings.icon_max_dimension_pixels,
            max_pixels=icon_settings.icon_max_total_pixels,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded icon is malformed or has unsafe trailing content",
        ) from exc

    service = IconService(public_base_url=str(request.base_url).rstrip("/"))
    uploaded = await service.upload_icon(
        file_content=file_content,
        # Derive the stored extension from the verified bytes, not the untrusted
        # client filename (which could carry a .svg/.html suffix).
        filename=None,
        content_type=sniffed_type,
        user_id=user.id,
    )
    return IconUploadResponse(**uploaded.model_dump())


@router.get(
    "/public/icons/{icon_path:path}",
    operation_id="icon.public.get",
    summary="Get Public Icon",
    description="Fetch a previously uploaded public icon asset.",
)
async def get_public_icon(icon_path: str) -> Response:
    service = IconService()
    try:
        content = await service.read_icon(icon_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid icon path",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Icon not found",
        ) from exc

    # Serve only known-inert raster types as their real media type; anything else
    # (e.g. an SVG stored before this hardening) is served as a non-active
    # download so it can never execute on our origin. nosniff blocks MIME
    # confusion either way.
    guessed = mimetypes.guess_type(icon_path)[0]
    media_type = (
        guessed if guessed in _SAFE_ICON_MEDIA_TYPES else "application/octet-stream"
    )
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if media_type == "application/octet-stream":
        headers["Content-Disposition"] = "attachment"
    return Response(content=content, media_type=media_type, headers=headers)

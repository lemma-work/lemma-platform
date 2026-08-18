"""Answering an image request for a model that cannot see one.

Both image-returning tools — `view_image` and `pod_view_document_pages` — need
the same fallback: when the run's model has no vision, hand the bytes to a
configured vision model and return what it says instead of the image itself.
Keeping that in one place is what stops the two tools from drifting into
different failure messages for identical situations, and keeps the delegation
out of two files that are already over the size ratchet.

The tools own *what* they render; this owns *how a blind model is answered*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.agent.services.vision_service import (
    MAX_IMAGES_PER_CALL,
    VisionDescriptionError,
    VisionImage,
    VisionUnavailableError,
    describe_images,
)
from app.modules.agent.tools.context import BaseAgentContext

if TYPE_CHECKING:  # pragma: no cover - import cycle: workspace_cli imports this
    from app.modules.agent.tools.workspace_cli.models import ViewImageResponse

# Appended when no vision model is configured at all. Both tools say the same
# thing, because from the agent's side it is the same situation.
_NO_VISION_SUFFIX = (
    "This agent's model cannot read images directly, so a separate vision "
    "model is required to look at one."
)


async def describe_single_image(
    ctx: BaseAgentContext,
    *,
    data: bytes,
    media_type: str,
    file_path: str,
    source: str,
    instructions: str | None,
) -> "ViewImageResponse":
    """`view_image`'s answer when the model cannot take image content."""
    from app.modules.agent.tools.workspace_cli.models import ViewImageResponse

    try:
        description = await describe_images(
            [VisionImage(data=data, media_type=media_type, label=f"image {file_path}")],
            instructions=instructions,
            organization_id=getattr(ctx, "organization_id", None),
            user_id=ctx.user_id,
        )
    except VisionUnavailableError as exc:
        return ViewImageResponse(
            success=False,
            error=f"{exc} {_NO_VISION_SUFFIX}",
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(data),
        )
    except VisionDescriptionError as exc:
        return ViewImageResponse(
            success=False,
            error=str(exc),
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(data),
        )

    return ViewImageResponse(
        success=True,
        message=description,
        file_path=file_path,
        media_type=media_type,
        source=source,
        size_bytes=len(data),
    )


async def describe_document_pages(
    deps: BaseAgentContext,
    *,
    path: str,
    pages: list[Any],
    page_refs: list[dict[str, Any]],
    instructions: str | None,
) -> dict[str, Any]:
    """`pod_view_document_pages`'s answer, as words rather than pixels.

    Chunked because a request may span up to `pdf_render_max_pages_per_call`
    pages, which is more than one vision call should carry.
    """
    descriptions: list[dict[str, Any]] = []
    for start in range(0, len(pages), MAX_IMAGES_PER_CALL):
        chunk = pages[start : start + MAX_IMAGES_PER_CALL]
        try:
            text = await describe_images(
                [
                    VisionImage(
                        data=page.jpeg_bytes,
                        media_type="image/jpeg",
                        label=f"page {page.page_number} of {path}",
                    )
                    for page in chunk
                ],
                instructions=instructions,
                organization_id=getattr(deps, "organization_id", None),
                user_id=deps.user_id,
            )
        except VisionUnavailableError as exc:
            return {
                "success": False,
                "path": path,
                "error": (
                    f"{exc} This agent's model cannot read images, so PDF pages "
                    "cannot be viewed. Use `pod_read_file` to read the page "
                    "text instead."
                ),
            }
        except VisionDescriptionError as exc:
            return {"success": False, "path": path, "error": str(exc)}
        descriptions.append(
            {
                "pages": [page.page_number for page in chunk],
                "description": text,
            }
        )

    return {
        "success": True,
        "path": path,
        "pages": page_refs,
        "viewed_by": "vision_model",
        "descriptions": descriptions,
    }

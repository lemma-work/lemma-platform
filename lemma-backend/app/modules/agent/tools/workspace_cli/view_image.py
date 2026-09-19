"""Showing the model an image, from either store.

Its own module because it is the one thing in this package that is not about
running a command, and `workspace_cli` sits against the architecture ratchet's
file-size limit. Re-exported from `workspace_cli` so no caller has to care.
"""

from __future__ import annotations

import mimetypes

from pydantic_ai import BinaryContent, ToolReturn

from app.core.domain.errors import DomainError
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.image_payload import downscale_for_vision
from app.modules.agent.tools.vision_delegation import describe_single_image
from app.modules.agent.tools.file_access import (
    read_pod_file_bytes,
    read_workspace_file_bytes,
)
from app.modules.agent.tools.tool_errors import (
    approval_error_result,
    safe_error_text,
)
from app.modules.agent.tools.workspace_cli.models import (
    ExecCommandResult,
    ViewImageRequest,
    ViewImageResponse,
)

#: What one image may weigh once decoded for the model. Providers reject
#: larger payloads outright, so the refusal is better made here, where it
#: can name the file and the limit.
MAX_VIEW_IMAGE_BYTES = 5 * 1024 * 1024


async def view_image_internal(
    ctx: BaseAgentContext,
    request: ViewImageRequest,
):
    # Require exactly one store path, returning a structured error (never raising)
    # so a wrong call surfaces success=False to the model instead of aborting the
    # run or burning the retry budget. Pick the store the agent explicitly
    # addressed — no path-shape inference.
    pod_path = (request.pod_file_path or "").strip()
    workspace_path = (request.workspace_file_path or "").strip()
    if bool(pod_path) == bool(workspace_path):
        return ViewImageResponse(
            success=False,
            error=(
                "Provide exactly one of `pod_file_path` (datastore) or "
                "`workspace_file_path` (sandbox)."
            ),
        )
    if pod_path:
        file_path = pod_path
        source = "datastore"
    else:
        file_path = workspace_path
        source = "workspace"

    try:
        if source == "datastore":
            content, detected_mime = await read_pod_file_bytes(ctx, file_path)
        else:
            content, detected_mime = await read_workspace_file_bytes(ctx, file_path)
    except DomainError as exc:
        # Datastore reads are grant-checked; surface a missing grant as
        # needs_approval so the agent can request access, like the pod tools.
        return approval_error_result(
            exc, tool_name="view_image", args=request.model_dump()
        )
    except Exception as exc:
        return ExecCommandResult(success=False, error=safe_error_text(exc))

    media_type = detected_mime or mimetypes.guess_type(file_path)[0]
    if not media_type or not media_type.startswith("image/"):
        if media_type == "application/pdf" or file_path.lower().endswith(".pdf"):
            hint = (
                "This is a PDF, not an image. Use `pod_view_document_pages` to see "
                "pages (layout, tables, figures), or `pod_read_file` to read "
                "the text."
            )
        else:
            hint = (
                f"This file is not an image (detected type: {media_type or 'unknown'}). "
                "`view_image` only handles image files. For documents, use "
                "`pod_read_file`; for PDFs, `pod_view_document_pages`."
            )
        return ViewImageResponse(
            success=False,
            error=hint,
            file_path=file_path,
            media_type=media_type,
            source=source,
        )

    # Sized for the model before it is measured against the limit. A phone
    # photo is several megabytes of pixels the model shrinks on arrival and
    # never looks at — so refusing it and telling the agent to go and compress
    # it was work nobody needed to do, on an image we were about to shrink
    # ourselves. What is left after this is what a limit should be judging.
    payload, payload_media_type = downscale_for_vision(content, media_type)
    if len(payload) > MAX_VIEW_IMAGE_BYTES:
        return ViewImageResponse(
            success=False,
            error=(
                f"Image is {len(payload) // 1024} KB even after downscaling, "
                f"over the {MAX_VIEW_IMAGE_BYTES // (1024 * 1024)} MB limit. "
                "Crop it or split it up before viewing."
            ),
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(content),
        )

    # Only a model that can actually accept image parts is given them. Handing
    # BinaryContent to a text-only model poisons the whole request, and the
    # provider rejects the turn rather than the tool call.
    if getattr(ctx, "vision_mode", AgentVisionMode.UNAVAILABLE) is not (
        AgentVisionMode.DIRECT
    ):
        # The delegate is a vision model too, and pays the same way for pixels
        # past its own ceiling.
        return await describe_single_image(
            ctx,
            data=payload,
            media_type=payload_media_type,
            file_path=file_path,
            source=source,
            instructions=request.instructions,
        )

    return ToolReturn(
        return_value=ViewImageResponse(
            success=True,
            message=f"Successfully read image {file_path}",
            file_path=file_path,
            media_type=media_type,
            source=source,
            size_bytes=len(content),
        ),
        content=[
            BinaryContent(data=payload, media_type=payload_media_type),
        ],
    )

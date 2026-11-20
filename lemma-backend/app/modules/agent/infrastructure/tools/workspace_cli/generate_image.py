"""Making an image and saving it on the agent's machine.

Its own module for the reason `view_image` has one: `workspace_cli` sits against
the file-size ratchet. The picture is written to the workspace, where the agent
can look at it (`view_image`), edit it, or copy it into the pod (`sync_files`).
"""

from __future__ import annotations

import posixpath

from pydantic import BaseModel, Field

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ImagePart
from app.modules.agent.infrastructure.tools.file_access import (
    read_workspace_file_bytes,
)
from app.modules.agent.infrastructure.tools.tool_errors import safe_error_text
from app.modules.agent.domain.vision import (
    ImageGenerationError,
    ImageModelUnavailableError,
)
from app.modules.agent.services.image_generation_service import (
    MAX_REFERENCE_IMAGES,
    generate_image,
)
from sandbox_runtime.errors import SandboxError

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class GenerateImageRequest(BaseModel):
    prompt: str = Field(
        description=(
            "What the image shows, in detail: subject, composition, style, "
            "colours, any text that must appear in it, and its aspect (for "
            "example 'wide 16:9 banner')."
        )
    )
    workspace_file_path: str = Field(
        description=(
            "Where to save it on your machine, relative to the conversation's "
            "directory, e.g. `images/banner.png`. The extension is set from "
            "what the model returns."
        )
    )
    reference_image_paths: list[str] = Field(
        default_factory=list,
        max_length=MAX_REFERENCE_IMAGES,
        description=(
            "Images on your machine to work from: one to edit, or a few to "
            "combine or keep a subject consistent with."
        ),
    )


class GenerateImageResult(BaseModel):
    success: bool
    error: str | None = None
    workspace_file_path: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None


async def generate_image_internal(
    ctx: ToolContext, request: GenerateImageRequest
) -> GenerateImageResult:
    target = request.workspace_file_path.strip()
    if not target or target.endswith("/"):
        return GenerateImageResult(success=False, error="Name the file to save.")
    try:
        references = [
            await _reference(ctx, path) for path in request.reference_image_paths
        ]
    except (FileNotFoundError, ValueError) as exc:
        return GenerateImageResult(success=False, error=safe_error_text(exc))
    try:
        image = await generate_image(
            request.prompt,
            references=references,
            organization_id=getattr(ctx, "organization_id", None),
            user_id=ctx.user_id,
            pod_id=getattr(ctx, "pod_id", None),
        )
    except (ImageModelUnavailableError, ImageGenerationError) as exc:
        return GenerateImageResult(success=False, error=str(exc))
    path = _with_extension(target, image.media_type)
    try:
        await _write_image(ctx, path, image.data)
    except (SandboxError, OSError, ValueError) as exc:
        return GenerateImageResult(success=False, error=safe_error_text(exc))
    return GenerateImageResult(
        success=True,
        workspace_file_path=path,
        media_type=image.media_type,
        size_bytes=len(image.data),
    )


async def _reference(ctx: ToolContext, path: str) -> ImagePart:
    content, media_type = await read_workspace_file_bytes(ctx, path)
    if not media_type or not media_type.startswith("image/"):
        raise ValueError(f"{path} is not an image.")
    return ImagePart(data=content, media_type=media_type)


def _with_extension(path: str, media_type: str) -> str:
    extension = _EXTENSIONS.get(media_type)
    if extension is None:
        return path
    stem, current = posixpath.splitext(path)
    return path if current.lower() == extension else f"{stem}{extension}"


async def _write_image(ctx: ToolContext, path: str, data: bytes) -> None:
    try:
        await ctx.file_manager.write_file(path, data)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Couldn't save to {path}: create its folder first "
            "(`mkdir -p`), or save it at the top level."
        ) from None

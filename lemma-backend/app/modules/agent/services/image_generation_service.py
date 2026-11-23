"""Making an image for an agent, with a model that draws.

Resolved by `image_runtime` (only a model that draws), built through the
model port, and metered as `image_generation` against whoever the run's usage
context says is spending -- an image model bills its pictures as image-output
tokens, and the ledger records each request that drew as `IMAGE` usage with the
pictures it returned as units.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.agent.domain.background import (
    BackgroundCall,
    BackgroundLimits,
    PromptPart,
)
from app.modules.agent.domain.tools import ImagePart
from app.modules.agent.domain.vision import (
    ImageGenerationError,
    ImageModelUnavailableError,
)
from app.modules.usage.contracts.execution import (
    UsageExecutionContext,
    current_usage_context,
)

#: Reference pictures one request may carry. Enough to restyle, combine or
#: keep a subject consistent; more is mostly payload.
MAX_REFERENCE_IMAGES = 3
MAX_REFERENCE_BYTES = 15 * 1024 * 1024
MAX_PROMPT_CHARS = 4000
#: An image model thinks before it draws; a minute is generous for one picture.
IMAGE_TIMEOUT_SECONDS = 90.0

_INSTRUCTIONS = (
    "Make exactly one image that fulfils the request. Reference images, if "
    "any, are given after it, in order."
)


async def generate_image(
    prompt: str,
    *,
    references: Sequence[ImagePart] = (),
    organization_id: UUID | None,
    user_id: UUID,
    pod_id: UUID | None = None,
) -> ImagePart:
    """One image for ``prompt``, drawn by the deployment's or workspace's
    image model.

    Raises ``ImageModelUnavailableError`` when there is no model that draws,
    ``ImageGenerationError`` for anything the caller should be told plainly.
    """
    text = prompt.strip()
    if not text:
        raise ImageGenerationError("Say what the image should show.")
    if len(text) > MAX_PROMPT_CHARS:
        raise ImageGenerationError(
            f"The description is {len(text)} characters; keep it under "
            f"{MAX_PROMPT_CHARS}."
        )
    if len(references) > MAX_REFERENCE_IMAGES:
        raise ImageGenerationError(
            f"At most {MAX_REFERENCE_IMAGES} reference images per image."
        )
    if sum(len(image.data) for image in references) > MAX_REFERENCE_BYTES:
        raise ImageGenerationError(
            "The reference images are too large together; "
            f"the limit is {MAX_REFERENCE_BYTES // (1024 * 1024)} MB."
        )

    # Imported here, as the vision delegate does: resolution reaches the
    # profile service, which reaches back into services that import the harness.
    from app.modules.agent.services.background_model import BackgroundModel
    from app.modules.agent.services.image_runtime import resolve_image_runtime

    resolved = await resolve_image_runtime(
        organization_id=organization_id, user_id=user_id
    )
    background = BackgroundModel()
    model = background.build(resolved)
    if model is None:
        raise ImageModelUnavailableError(
            "The image model could not be built from its provider settings."
        )

    current = current_usage_context()
    usage = (
        current
        if current is not None
        else UsageExecutionContext(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            source_type="image_generation",
        )
    )
    call_prompt: list[PromptPart] = [text, *references]
    try:
        return await background.draw(
            model,
            BackgroundCall(
                prompt=call_prompt,
                limits=BackgroundLimits(request_limit=1),
                instructions=_INSTRUCTIONS,
            ),
            usage=usage,
            timeout_seconds=IMAGE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise ImageGenerationError(
            f"The image model did not finish within {IMAGE_TIMEOUT_SECONDS:.0f}s."
        ) from exc

"""Let an agent on a text-only model still look at something.

A separate vision model reads the image and reports what it sees in words. The
calling agent's own history therefore never contains image content, which is
what makes this safe on a text-only model — the failure it replaces is a
provider 400 when `BinaryContent` reaches a model that cannot accept it.

Fallback policy is deliberately the opposite of `conversation_title_service`'s:
titles fall back to the profile default, vision must not. Falling back here
would resolve to the same text-only model that created the problem, and the
400 would come back wearing a different hat. If no vision-capable model is
configured, the tool says so.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai import BinaryContent
from pydantic_ai.messages import UserContent
from pydantic_ai import UsageLimits

from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings

logger = get_logger(__name__)

# Generous relative to a normal tool call, because a multi-page render on a slow
# provider is legitimately slow — but well under the sub-agent budget (300s), so
# a stuck vision call cannot eat a whole turn.
VISION_TIMEOUT_SECONDS = 120
MAX_IMAGES_PER_CALL = 8
MAX_TOTAL_IMAGE_BYTES = 16 * 1024 * 1024

_SYSTEM_PROMPT = (
    "You are the eyes of another agent that cannot see images. Answer only "
    "from what is visible. Transcribe text and tables verbatim, preserving "
    "structure. For diagrams and charts, name every node, label and axis, and "
    "state the direction of every arrow or relationship. If a detail the "
    "caller asked about is not legible, say so plainly rather than guessing."
)

_DEFAULT_INSTRUCTIONS = (
    "Describe this image completely enough that someone who cannot see it "
    "could act on it. Transcribe any text, tables, or diagram labels."
)


class VisionUnavailableError(RuntimeError):
    """No vision-capable model is configured for this deployment."""


class VisionDescriptionError(RuntimeError):
    """The vision model was reachable but could not produce a description."""


@dataclass(frozen=True)
class VisionImage:
    """One image to look at, with a label so the model can refer to it."""

    data: bytes
    media_type: str
    label: str


def configured_vision_model_name() -> str | None:
    """Env wins over settings, matching `runtime_profile_service`'s idiom."""
    raw = os.getenv("VISION_MODEL")
    if raw is None:
        raw = agent_settings.vision_model
    value = (raw or "").strip()
    return value or None


def vision_delegate_available() -> bool:
    return configured_vision_model_name() is not None


async def _resolve_vision_model(*, organization_id: UUID | None, user_id: UUID):
    """A vision-capable pydantic-ai model, or raise.

    Asserts the resolved catalog entry actually declares VISION so a misconfigured
    `VISION_MODEL` fails here, with a message naming the setting, rather than as
    an opaque provider error several layers down.
    """
    model_name = configured_vision_model_name()
    if not model_name:
        raise VisionUnavailableError(
            "No vision model is configured (set VISION_MODEL)."
        )

    # AgentRuntimeConfig lives in the shared runtime module (re-exported by
    # value_objects); only RuntimeModelCapability is defined in runtime_profiles.
    from app.core.domain.runtime import AgentRuntimeConfig
    from app.modules.agent.domain.runtime_profiles import RuntimeModelCapability
    from app.modules.agent.services.runtime_model_factory import (
        pydantic_ai_model_from_runtime_profile,
    )
    from app.modules.agent.services.runtime_profile_service import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeProfileService,
    )

    resolved = await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            model_name=model_name,
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    entry = resolved.model
    if entry is not None and RuntimeModelCapability.VISION not in entry.capabilities:
        raise VisionUnavailableError(
            f"VISION_MODEL is set to '{model_name}', which this deployment does "
            "not list as accepting images. Add it to "
            "LEMMA_OPENAI_VISION_MODEL_NAMES or point VISION_MODEL at a model "
            "that does."
        )
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials,
    )
    if model is None:
        raise VisionUnavailableError(
            f"VISION_MODEL '{model_name}' could not be built from the runtime profile."
        )
    return model


def _validate(images: Sequence[VisionImage]) -> None:
    if not images:
        raise VisionDescriptionError("No images were provided.")
    if len(images) > MAX_IMAGES_PER_CALL:
        raise VisionDescriptionError(
            f"Too many images in one call ({len(images)}); the limit is "
            f"{MAX_IMAGES_PER_CALL}. Ask for fewer pages at a time."
        )
    total = sum(len(image.data) for image in images)
    if total > MAX_TOTAL_IMAGE_BYTES:
        raise VisionDescriptionError(
            "The images are too large to analyse in one call "
            f"({total // (1024 * 1024)}MB); the limit is "
            f"{MAX_TOTAL_IMAGE_BYTES // (1024 * 1024)}MB."
        )


async def describe_images(
    images: Sequence[VisionImage],
    *,
    instructions: str | None,
    organization_id: UUID | None,
    user_id: UUID,
) -> str:
    """Return a text description of ``images`` from the configured vision model."""
    _validate(images)
    model = await _resolve_vision_model(
        organization_id=organization_id, user_id=user_id
    )

    prompt: list[UserContent] = [
        (instructions or _DEFAULT_INSTRUCTIONS).strip(),
        "",
        "Images, in order:",
    ]
    for index, image in enumerate(images, start=1):
        prompt.append(f"{index}. {image.label}")
    for image in images:
        prompt.append(BinaryContent(data=image.data, media_type=image.media_type))

    agent = PydanticAIAgent(model, instructions=_SYSTEM_PROMPT)
    try:
        async with asyncio.timeout(VISION_TIMEOUT_SECONDS):
            result = await agent.run(
                prompt,
                usage_limits=UsageLimits(request_limit=1, output_tokens_limit=4096),
            )
    except TimeoutError as exc:
        raise VisionDescriptionError(
            f"The vision model did not respond within {VISION_TIMEOUT_SECONDS}s."
        ) from exc
    except Exception as exc:
        logger.warning(
            "agent.vision_service.description_failed.degraded", exc_info=True
        )
        raise VisionDescriptionError(
            "The vision model could not describe the image."
        ) from exc

    description = (result.output or "").strip()
    if not description:
        raise VisionDescriptionError("The vision model returned no description.")
    return description

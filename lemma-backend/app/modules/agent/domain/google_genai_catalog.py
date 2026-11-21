"""What a GOOGLE_GENAI profile's models can do, declared rather than discovered.

Google's model listing reports neither image input nor thinking, and lists
embedding, speech and image models beside the chat ones. So a Google profile's
catalog is the names its owner gives, split in two: chat models, and image
models that draw for the `generate_image` tool.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.domain.value_objects import JsonObject

#: Every Gemini chat model reads images and thinks.
GOOGLE_CHAT_MODEL_CAPABILITIES = (
    RuntimeModelCapability.TEXT,
    RuntimeModelCapability.TOOLS,
    RuntimeModelCapability.VISION,
    RuntimeModelCapability.REASONING,
)
#: An image model reads the prompt and any reference images, and draws. No
#: TOOLS: it is never an agent's chat model.
GOOGLE_IMAGE_MODEL_CAPABILITIES = (
    RuntimeModelCapability.TEXT,
    RuntimeModelCapability.VISION,
    RuntimeModelCapability.IMAGE_OUTPUT,
)

#: What a new Google provider offers when its owner names no models.
DEFAULT_GOOGLE_CHAT_MODELS = (
    "gemini-4-hydrogen",
    "gemini-4-argon",
    "gemini-4-carbon",
    "gemini-3.8-flash",
)
DEFAULT_GOOGLE_IMAGE_MODELS = ("gemini-3.1-flash-image",)


def google_genai_catalog(
    chat_model_names: Sequence[str],
    image_model_names: Sequence[str],
    *,
    display_name: Callable[[str], str | None] = lambda _name: None,
    metadata_for: Callable[[str], JsonObject] = lambda _name: {},
) -> list[RuntimeModelCatalogEntry]:
    """Chat models first, in the order given, then the image models.

    A name in both lists is refused: nothing here can tell a chat model from
    an image one, and guessing either way is wrong -- an image model offered
    as a chat model fails every turn, a chat model filed as an image model can
    never run one.
    """
    chat = _unique(chat_model_names)
    images = _unique(image_model_names)
    both = [name for name in images if name in chat]
    if both:
        raise RuntimeProfileValidationError(
            f"{', '.join(both)} is listed both as a chat model and as an image "
            "model; list each model once."
        )
    return [
        *(
            _entry(name, GOOGLE_CHAT_MODEL_CAPABILITIES, display_name, metadata_for)
            for name in chat
        ),
        *(
            _entry(name, GOOGLE_IMAGE_MODEL_CAPABILITIES, display_name, metadata_for)
            for name in images
        ),
    ]


def is_image_model(entry: RuntimeModelCatalogEntry) -> bool:
    return RuntimeModelCapability.IMAGE_OUTPUT in entry.capabilities


def _entry(
    name: str,
    capabilities: Sequence[RuntimeModelCapability],
    display_name: Callable[[str], str | None],
    metadata_for: Callable[[str], JsonObject],
) -> RuntimeModelCatalogEntry:
    return RuntimeModelCatalogEntry(
        name=name,
        display_name=display_name(name),
        provider_model_name=name,
        capabilities=list(capabilities),
        metadata=metadata_for(name),
    )


def _unique(names: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for raw in names:
        name = raw.strip()
        if name and name not in seen:
            seen.append(name)
    return seen

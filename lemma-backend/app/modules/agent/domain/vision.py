"""How this run is able to look at an image.

Vision used to be all-or-nothing and enforced in one place: `view_image` was
appended only when the resolved model declared VISION. Everything else that
returns image content — notably `pod_view_document_pages`, which renders PDF
pages so an agent can read diagrams and tables — shipped to every model
regardless. With `LEMMA_OPENAI_VISION_MODEL_NAMES` defaulting to empty, the
common deployment therefore *withheld* the safe tool and *offered* the unsafe
one, and a text-only model got image content in its history and returned 400.

Modelling it as a mode instead of a boolean lets the tools stay present for
every agent and choose how to answer:

    DIRECT       the model reads the image itself
    DELEGATED    a configured vision model reads it and reports back in words
    UNAVAILABLE  neither is possible; say so instead of failing at the provider

The tools' schemas are identical in all three, which matters more than it looks:
the prompt prefix stays byte-identical across models, so prompt caching and the
deferred-tools hint do not fork per model.
"""

from __future__ import annotations

from enum import Enum


class AgentVisionMode(str, Enum):
    DIRECT = "DIRECT"
    DELEGATED = "DELEGATED"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def can_see(self) -> bool:
        """Whether an image can be interpreted at all, by any route."""
        return self is not AgentVisionMode.UNAVAILABLE


def resolve_vision_mode(
    *,
    model_supports_vision: bool,
    delegate_model_configured: bool,
) -> AgentVisionMode:
    if model_supports_vision:
        return AgentVisionMode.DIRECT
    if delegate_model_configured:
        return AgentVisionMode.DELEGATED
    return AgentVisionMode.UNAVAILABLE


def vision_mode_from_runtime_profile(
    runtime_profile: object,
) -> AgentVisionMode:
    """Derive the mode from a persisted runtime-profile snapshot.

    Used by the MCP bridges, which rebuild a context from the stored snapshot
    rather than from a freshly resolved runtime. Falls back to the delegate when
    the snapshot predates ``model_capabilities`` — old runs then behave as they
    did before rather than losing image support outright.
    """
    from app.modules.agent.services.vision_service import vision_delegate_available

    capabilities = (
        runtime_profile.get("model_capabilities")
        if isinstance(runtime_profile, dict)
        else None
    )
    supports_vision = isinstance(capabilities, (list, tuple)) and any(
        str(capability).upper() == "VISION" for capability in capabilities
    )
    return resolve_vision_mode(
        model_supports_vision=supports_vision,
        delegate_model_configured=vision_delegate_available(),
    )

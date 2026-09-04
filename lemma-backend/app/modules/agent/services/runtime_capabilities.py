"""What a runtime can do, worked out separately from which model it runs.

Both answers used to be read off the selected catalog entry, so a profile
that pinned no model -- the normal state for Agent Host, where the harness
picks -- reported no capabilities at all and was treated as unable to read
an image. Keeping the derivation here keeps that distinction visible: model
*selection* is the caller's business, model *capability* is not.
"""

from __future__ import annotations

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)


def with_harness_vision(
    model: RuntimeModelCatalogEntry | None,
    *,
    harness_sees: bool,
) -> RuntimeModelCatalogEntry | None:
    """Additive only: a harness that reports images gains VISION.

    One that does not is left exactly as stored, because the stored catalog is
    what an operator may have deliberately edited.
    """
    if (
        model is None
        or not harness_sees
        or RuntimeModelCapability.VISION in model.capabilities
    ):
        return model
    return model.model_copy(
        update={"capabilities": [*model.capabilities, RuntimeModelCapability.VISION]}
    )


def unselected_capabilities(
    profile: AgentRuntimeProfile,
    *,
    harness_sees: bool,
) -> list[RuntimeModelCapability]:
    """What the runtime can do when no catalog entry is selected.

    An Agent Host profile routinely pins no model: `agent_host_model_catalog`
    documents an empty catalog as meaning "let the harness use its own default",
    and a populated catalog with no chosen entry means the same. Either way
    `_selected_model` returns None, and reading capabilities off that None was
    reporting every such runtime as unable to see.

    The catalog is still the better source when it has entries, so it is used --
    but by **intersection**, so a mixed catalog cannot claim a capability only
    some of its models have. `harness_sees` is then additive on top, exactly as
    it is for a selected model.
    """
    baseline = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if profile.model_catalog:
        shared = set(profile.model_catalog[0].capabilities)
        for entry in profile.model_catalog[1:]:
            shared &= set(entry.capabilities)
        capabilities = [c for c in profile.model_catalog[0].capabilities if c in shared]
    else:
        capabilities = list(baseline)
    if harness_sees and RuntimeModelCapability.VISION not in capabilities:
        capabilities.append(RuntimeModelCapability.VISION)
    return capabilities

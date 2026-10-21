"""What a resolved runtime is, and which catalog model a run gets.

Split from ``runtime_profile_service`` by use case: listing and resolving
profiles lives there; the resolved value and the pure model choice live here,
so both can be read and tested without the repository behind them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.runtime_snapshot import (
    RuntimeSnapshot as RuntimeSnapshot,
)
from app.modules.agent.domain.value_objects import HarnessKind

logger = get_logger(__name__)


class RuntimeHolder(StrEnum):
    """What a runtime is being saved on, in the words the refusal uses."""

    TEAMMATE = "teammate"
    CONVERSATION = "conversation"


@dataclass(slots=True)
class ResolvedAgentRuntime:
    profile: AgentRuntimeProfile
    harness_kind: HarnessKind
    model: RuntimeModelCatalogEntry | None
    provider_model_name: str | None
    # Revealed plaintext, for the harness alone; kept out of the repr so a
    # logged or raised ResolvedAgentRuntime does not print the key.
    credentials: dict[str, object] | None = field(repr=False)
    # What the runtime can do when no catalog entry is selected. A harness
    # profile routinely pins no model -- the harness picks its own -- and
    # capabilities must survive that, because "nothing selected" says nothing
    # about whether the thing on the other end can read an image.
    unselected_capabilities: list[RuntimeModelCapability] = field(default_factory=list)
    # The run's own Agent Host selections -- pod default, teammate and
    # conversation, already layered -- laid over the profile's at dispatch.
    selection_overrides: dict[str, str] = field(default_factory=dict)

    @property
    def capabilities(self) -> list[RuntimeModelCapability]:
        """What this runtime can do, selected model or not.

        Read this rather than `model.capabilities`: an Agent Host profile
        routinely selects no model, and `model is None` says nothing about
        whether the thing on the other end can read an image.
        """
        return list(
            self.model.capabilities if self.model else self.unselected_capabilities
        )

    @property
    def model_name_for_harness(self) -> str:
        if self.model is None:
            return "default"
        return self.provider_model_name or self.model.name

    def runtime_snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            profile_id=self.profile.id,
            profile_name=self.profile.name,
            user_id=str(self.profile.user_id) if self.profile.user_id else None,
            harness_id=str(self.profile.harness_id)
            if self.profile.harness_id
            else None,
            scope=self.profile.scope,
            protocol=self.profile.protocol,
            model_name=self.model.name if self.model else None,
            provider_model_name=self.provider_model_name,
            model_metadata=dict(self.model.metadata) if self.model else {},
            # Carried so paths that rebuild a context from the snapshot — the
            # MCP bridges, notably — can work out whether this model reads
            # images, instead of assuming it cannot and delegating needlessly.
            # Sourced from the selected model when there is one, and from the
            # runtime itself when there is not. It used to be `[] if not
            # self.model`, which read "this model cannot see" for every Agent
            # Host run -- those pin no model -- so `pod_view_document_pages`
            # refused and `view_image` was withheld from hosts that read images
            # natively.
            model_capabilities=[capability.value for capability in self.capabilities],
            config=_config_dict(self.profile.config),
            selection_overrides=dict(self.selection_overrides),
        )


def selected_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
    *,
    saving_for: RuntimeHolder | None = None,
) -> RuntimeModelCatalogEntry | None:
    """The catalog entry a run uses, or ``None`` when nothing is pinned.

    Never a substitute. A model the profile no longer offers used to be
    swapped for its default, or its first entry, with only a log line: a
    teammate pinned to one model ran -- and was billed -- on another, with
    different behaviour, indefinitely and invisibly. Now the run is refused
    with the reason, and the person picks the model.

    The one ``None`` that is not a refusal: a coding agent whose catalog is
    empty publishes no list to check against, and an unpinned profile there
    means "the agent's own default".

    ``saving_for`` names what the runtime is being saved on, when it is. A
    save may name a model the profile never offered at all, so only a turn --
    on a pin a save already accepted -- can say the model went away.
    """
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    if profile.harness_id is not None and not profile.model_catalog:
        return None
    logger.warning(
        "agent.runtime_profile.model_unavailable.refused",
        profile_id=profile.id,
        requested_model_name=model_name,
    )
    raise model_unavailable_error(profile.name, model_name, saving_for=saving_for)


def model_unavailable_error(
    profile_name: str,
    model_name: str,
    *,
    saving_for: RuntimeHolder | None = None,
) -> RuntimeUnavailableError:
    if saving_for is not None:
        return RuntimeUnavailableError(
            f"{profile_name} does not offer a model named {model_name}. Pick "
            f"one of its models for this {saving_for.value}, or refresh the "
            "model list in Settings → Models."
        )
    # A turn's runtime is the conversation's own pin or its teammate's, and
    # either can be the stale one -- so the remedy names both.
    return RuntimeUnavailableError(
        f"{profile_name} no longer offers {model_name}, the model this "
        "conversation is set to use. Pick another model for the teammate or "
        "the conversation, or refresh the model list in Settings → Models."
    )


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    if isinstance(config, BaseModel):
        return config.model_dump(mode="json")
    if isinstance(config, dict):
        return config
    return {}

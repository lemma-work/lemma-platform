"""What a resolved runtime is: the profile, the model and what it can do.

Pure values. Which runtime a run gets is decided in
``services/runtime_resolution``; this is the answer it hands back, and what a
run's harness, tools and metering read from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel

from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.domain.value_objects import HarnessKind


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

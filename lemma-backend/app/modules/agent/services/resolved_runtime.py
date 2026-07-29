"""Resolved runtime value used by the runner and harness selection."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCatalogEntry,
)
from app.modules.agent.domain.value_objects import HarnessKind


@dataclass(slots=True)
class ResolvedAgentRuntime:
    profile: AgentRuntimeProfile
    harness_kind: HarnessKind
    model: RuntimeModelCatalogEntry | None
    provider_model_name: str | None
    credentials: dict[str, object] | None

    @property
    def model_name_for_harness(self) -> str:
        if self.model is None:
            return "default"
        return self.provider_model_name or self.model.name

    def public_snapshot(self) -> dict[str, object | None]:
        return {
            "profile_id": self.profile.id,
            "profile_name": self.profile.name,
            "user_id": str(self.profile.user_id) if self.profile.user_id else None,
            "daemon_id": (
                str(self.profile.daemon_id) if self.profile.daemon_id else None
            ),
            "host_integration_id": (
                str(self.profile.host_integration_id)
                if self.profile.host_integration_id
                else None
            ),
            "scope": self.profile.scope.value,
            "protocol": self.profile.protocol.value,
            "model_name": self.model.name if self.model else None,
            "provider_model_name": self.provider_model_name,
            "config": _config_dict(self.profile.config),
        }


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json")
        return value if isinstance(value, dict) else {}
    return config if isinstance(config, dict) else {}

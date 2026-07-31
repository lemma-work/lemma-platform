"""System-level agent runtime defaults."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.core.config import settings
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)


class AgentRuntimeDefaultError(ValueError):
    """Raised when a local runtime default cannot be updated."""


class AgentRuntimeDefaultService:
    def __init__(
        self,
        *,
        environment: str | None = None,
        config_path: Path | str | None = None,
    ):
        self.environment = environment or settings.environment
        self.config_path = Path(
            config_path or agent_settings.local_agent_runtime_config_path
        ).expanduser()

    @property
    def editable(self) -> bool:
        return self.environment == "local"

    def get_default(self) -> AgentRuntimeConfig:
        if self.environment == "local":
            configured = self._read_local_config()
            if configured is not None:
                return configured
            return AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            )
        return AgentRuntimeConfig(
            profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        )

    def set_default(self, agent_runtime: AgentRuntimeConfig) -> AgentRuntimeConfig:
        if not self.editable:
            raise AgentRuntimeDefaultError(
                "Default agent runtime can only be changed in local environment"
            )
        self._validate_runtime(agent_runtime)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(agent_runtime.model_dump(mode="json"), indent=2) + "\n"
        self.config_path.write_text(payload, encoding="utf-8")
        return agent_runtime

    def _read_local_config(self) -> AgentRuntimeConfig | None:
        if not self.config_path.exists():
            return None
        try:
            return AgentRuntimeConfig.model_validate_json(
                self.config_path.read_text(encoding="utf-8")
            )
        except OSError, ValidationError, ValueError:
            return None

    def _validate_runtime(self, agent_runtime: AgentRuntimeConfig) -> None:
        if not agent_runtime.profile_id:
            raise AgentRuntimeDefaultError("Default runtime requires profile_id")

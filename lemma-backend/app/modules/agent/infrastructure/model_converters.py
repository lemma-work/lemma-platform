"""Persistence conversion helpers shared by agent SQLAlchemy models."""

from app.modules.agent.domain.runtime_profiles import SYSTEM_LEMMA_PROFILE_ID
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentToolset,
    JsonObject,
)


def default_agent_runtime() -> JsonObject:
    """The column default for a run inserted without a runtime.

    Every run the application creates names its runtime; this only fills the
    NOT NULL column for a row written some other way.
    """
    return {"profile_id": SYSTEM_LEMMA_PROFILE_ID}


def agent_runtime_from_json(data: JsonObject | None) -> AgentRuntimeConfig | None:
    return AgentRuntimeConfig.model_validate(data) if data is not None else None


def coerce_toolsets(raw: list[str] | None) -> list[AgentToolset]:
    """Convert stored toolsets while tolerating values retired from the enum."""
    toolsets: list[AgentToolset] = []
    for value in raw or []:
        try:
            toolsets.append(AgentToolset(value))
        except ValueError:
            continue
    return toolsets

"""Persistence conversion helpers shared by agent SQLAlchemy models."""

from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentToolset,
)


def default_agent_runtime() -> dict:
    return {"profile_id": "system:lemma"}


def agent_runtime_from_json(data: dict | None) -> AgentRuntimeConfig | None:
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

"""An agent as the API returns it, and as a pod bundle exports it.

Defined here rather than in ``api/schemas`` because both read it: the agent
routes respond with it, and the pod bundle exporter writes the same shape so a
bundle imports back through the same route. The API schema module re-exports
it; a contract never reaches into the API layer for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.core.authorization.context import (
    ResourceVisibility,
    normalize_resource_visibility,
)

from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    AgentToolset,
    JsonObject,
)


def _stored_visibility(value: object) -> object:
    # Normalized on every write, but read back through the one function that
    # knows the older spellings, so a legacy row still answers.
    if isinstance(value, str):
        return normalize_resource_visibility(value) or value
    return value


#: The enum in the published schema, where a bare ``str`` told clients nothing.
StoredVisibility = Annotated[ResourceVisibility, BeforeValidator(_stored_visibility)]


class AgentResponse(BaseModel):
    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    # Which of these the pod came with. A client renders the default
    # assistant beside the agents somebody made, and has to be able to tell
    # them apart to withhold edit and delete -- it is the same list.
    kind: AgentKind = AgentKind.USER
    description: str | None = None
    icon_url: str | None = None
    visibility: StoredVisibility = ResourceVisibility.POD
    instruction: str
    agent_runtime: AgentRuntimeConfig | None = None
    toolsets: list[AgentToolset] = Field(default_factory=list)
    input_schema: JsonObject | None = None
    output_schema: JsonObject | None = None
    metadata: JsonObject | None = None
    machine: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

"""Request and response schemas for runtime profiles (Settings -> Models).

Split from ``schemas`` by use case: that module is agents and conversations,
and sits against the architecture ratchet's file-size limit. Re-exported there
so existing imports keep working.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.modules.agent.agent_host.api import AgentHostHarnessResponse
from app.modules.agent.agent_host.api import AgentHostStatus
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCatalogEntry,
    RuntimeProfileAvailability,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    HarnessKind,
    JsonObject,
)


class AgentRuntimeProfileResponse(BaseModel):
    id: str
    organization_id: UUID | None = None
    user_id: UUID | None = None
    harness_id: UUID | None = None
    scope: RuntimeProfileScope
    kind: RuntimeProfileKind
    protocol: RuntimeProfileProtocol
    name: str
    description: str | None = None
    default_model_name: str | None = None
    model_catalog: list[RuntimeModelCatalogEntry] = Field(default_factory=list)
    config: JsonObject = Field(default_factory=dict)
    status: RuntimeProfileStatus
    #: The coding agent an Agent Host profile runs (``codex``, ``claude-code``),
    #: as its computer published it; null for a model provider.
    harness_key: str | None = None
    #: Whether an owner chose this profile as what every teammate runs on.
    organization_default: bool = False
    has_credentials: bool = False
    derived_harness_kind: HarnessKind
    availability_status: RuntimeProfileAvailability | None = None


class AgentRuntimeProfileListResponse(BaseModel):
    items: list[AgentRuntimeProfileResponse]
    default_runtime: AgentRuntimeConfig
    # The model an owner chose for every teammate, or None when nobody has.
    # Separate from default_runtime, which is what a run gets either way and
    # may be a guess (the first provider) or the system model.
    organization_default_runtime: AgentRuntimeConfig | None = None


class SetOrganizationDefaultRuntimeRequest(BaseModel):
    profile_id: str = Field(min_length=1)
    # None follows the provider's own default model as it changes.
    model_name: str | None = Field(default=None, min_length=1)


class AgentRuntimeProfileTestResponse(BaseModel):
    ok: bool
    message: str
    models: list[str] | None = None


class CreateAgentHostRuntimeProfileRequest(BaseModel):
    source: Literal["AGENT_HOST"] = "AGENT_HOST"
    # From GET /me/runtime/agent-hosts/{id}/harnesses, which is what Lemma
    # Desktop lists under Models.
    harness_id: UUID
    # Personal unless asked for. Unlike a provider profile, which carries an
    # organization's own credential, this one points at a coding agent on one
    # person's machine and dispatches runs there whoever selects the model.
    # Omitting the field is not a decision to share a laptop with a workspace.
    scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    config_selections: JsonObject = Field(default_factory=dict)
    host_wait_timeout_seconds: int | None = Field(default=None, ge=1)


class CreateOpenAICompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["OPENAI_COMPATIBLE"] = "OPENAI_COMPATIBLE"
    name: str = Field(min_length=1, max_length=255)
    base_url: HttpUrl
    api_key: str | None = Field(default=None, min_length=1)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    # An OpenAI-compatible `/models` list rarely says which models take image
    # input, and the catalog treats silence as "cannot"; this is how the person
    # adding the provider says otherwise.
    vision_model_names: list[str] = Field(default_factory=list)
    # Which models reason -- take a reasoning effort or thinking budget. A
    # `/models` listing does not say, and an OpenAI-compatible endpoint rejects
    # the field on a model that does not, so it is declared, like vision.
    reasoning_model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


class CreateAnthropicCompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["ANTHROPIC_COMPATIBLE"] = "ANTHROPIC_COMPATIBLE"
    name: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    base_url: HttpUrl | None = None
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] = Field(default_factory=list)
    # Which models reason -- take a reasoning effort or thinking budget. A
    # `/models` listing does not say, and an OpenAI-compatible endpoint rejects
    # the field on a model that does not, so it is declared, like vision.
    reasoning_model_names: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    model_settings: JsonObject = Field(default_factory=dict)


CreateAgentRuntimeProfileRequest = Annotated[
    CreateAgentHostRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest,
    Field(discriminator="source"),
]


# Every field below is optional AND nullable, because for an edit those mean
# different things: omitting ``api_key`` keeps the stored one, sending null
# clears it. The controller reads ``model_fields_set`` to tell them apart -
# collapsing the two would silently destroy a credential on every rename.
# ``headers`` is deliberately absent: it can carry an authorization value, so it
# needs the same absent-vs-null discipline and no UI asks for it yet.


class UpdateAgentHostRuntimeProfileRequest(BaseModel):
    source: Literal["AGENT_HOST"] = "AGENT_HOST"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    default_model_name: str | None = Field(default=None, min_length=1)
    config_selections: JsonObject | None = None
    host_wait_timeout_seconds: int | None = Field(default=None, ge=1)


class UpdateOpenAICompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["OPENAI_COMPATIBLE"] = "OPENAI_COMPATIBLE"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] | None = None
    model_settings: JsonObject | None = None
    # Replaces the declared reasoning models; omitted keeps them, even across
    # a model refresh.
    reasoning_model_names: list[str] | None = None
    refresh_models: bool = False


class UpdateAnthropicCompatibleRuntimeProfileRequest(BaseModel):
    source: Literal["ANTHROPIC_COMPATIBLE"] = "ANTHROPIC_COMPATIBLE"
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    # Null resets to the default Anthropic endpoint - unlike the
    # OpenAI-compatible profile, which has no default to fall back to.
    base_url: HttpUrl | None = None
    api_key: str | None = Field(default=None, min_length=1)
    default_model_name: str | None = Field(default=None, min_length=1)
    model_names: list[str] | None = None
    model_settings: JsonObject | None = None
    # Replaces the declared reasoning models; omitted keeps them, even across
    # a model refresh.
    reasoning_model_names: list[str] | None = None
    refresh_models: bool = False


UpdateAgentRuntimeProfileRequest = Annotated[
    UpdateAgentHostRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest,
    Field(discriminator="source"),
]


class AgentRuntimeProfileDetailResponse(AgentRuntimeProfileResponse):
    """One profile plus the live harness it is bound to.

    An editor has to render the harness's *current* config options, not the ones
    the profile was saved against - those are what the edit will be validated
    and re-pinned to.
    """

    harness: AgentHostHarnessResponse | None = None
    host_status: AgentHostStatus | None = None

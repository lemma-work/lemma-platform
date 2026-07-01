from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
)
from app.modules.agent_surfaces.domain.setup_guides import (
    SurfaceSetupAction,
    SurfacePlatformSetupGuide,
)


class SurfaceIdentityConfigInput(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SurfaceChannelRouteInput(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_channel_ref(self) -> "SurfaceChannelRouteInput":
        if not self.channel_id and not self.channel_name:
            raise ValueError("channel_id or channel_name is required")
        return self


class SurfaceSendPolicyConfig(BaseModel):
    """Proactive-send controls. Mirrored across request and response."""

    allow_send: bool = False


class SurfaceBehaviorConfigInput(BaseModel):
    identity: SurfaceIdentityConfigInput = Field(default_factory=SurfaceIdentityConfigInput)
    channels: list[SurfaceChannelRouteInput] = Field(default_factory=list)
    dm_conversation_reset_after_hours: int = 24
    send_policy: SurfaceSendPolicyConfig = Field(default_factory=SurfaceSendPolicyConfig)

    model_config = ConfigDict(extra="forbid")


class SurfaceChannelRouteResponse(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None


class AvailableSurfaceChannelResponse(BaseModel):
    """A channel/group the surface bot can be configured to respond in."""

    id: str
    name: str | None = None
    is_member: bool | None = None


class AvailableSurfaceChannelsResponse(BaseModel):
    channels: list[AvailableSurfaceChannelResponse] = Field(default_factory=list)


class SurfaceIdentityConfigResponse(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)


class SurfaceConfigResponse(BaseModel):
    """Mirrors SurfaceBehaviorConfigInput: what you send is what you get back."""

    identity: SurfaceIdentityConfigResponse = Field(
        default_factory=SurfaceIdentityConfigResponse
    )
    channels: list[SurfaceChannelRouteResponse] = Field(default_factory=list)
    dm_conversation_reset_after_hours: int = 24
    send_policy: SurfaceSendPolicyConfig = Field(default_factory=SurfaceSendPolicyConfig)

    @classmethod
    def from_domain(cls, config: SurfaceConfig) -> "SurfaceConfigResponse":
        return cls.model_validate(config.model_dump(mode="json"))


def surface_config_from_input(
    config_input: SurfaceBehaviorConfigInput,
    *,
    channel_routes: list[SurfaceChannelRoute],
) -> SurfaceConfig:
    """Build the domain config from API input (channel routes pre-resolved
    from agent names by the controller)."""
    return SurfaceConfig(
        dm_conversation_reset_after_hours=config_input.dm_conversation_reset_after_hours,
        identity=SurfaceIdentityPolicy(
            allowed_domains=config_input.identity.allowed_domains,
            allowed_email_addresses=config_input.identity.allowed_email_addresses,
        ),
        channels=channel_routes,
        send_policy=SurfaceSendPolicy(allow_send=config_input.send_policy.allow_send),
    )


class SurfaceCreateRequest(BaseModel):
    """Body for `POST /pods/{pod_id}/surfaces` — creates one surface.

    A pod may have several surfaces of the same ``platform`` (different
    bots/accounts, each routed to its own agent); ``name`` is the stable,
    pod-unique identifier used to address it afterward. When omitted, it
    defaults to the lowercased platform (so the common single-surface-per-
    platform case needs no name at all) — pick an explicit name to create a
    second surface of the same platform.
    """

    platform: SurfacePlatform
    name: str | None = Field(
        default=None,
        description="Pod-unique surface identifier. Defaults to the lowercased platform.",
    )
    default_agent_name: str | None = None
    account_id: UUID | None = None
    credential_mode: SurfaceCredentialMode = SurfaceCredentialMode.SYSTEM
    config: SurfaceBehaviorConfigInput = Field(default_factory=SurfaceBehaviorConfigInput)
    is_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class SurfaceUpdateRequest(BaseModel):
    """Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

    Partial update (merge semantics): only fields present in the request are
    applied. The surface's ``platform`` and ``name`` are immutable — delete and
    recreate to change either.
    """

    default_agent_name: str | None = None
    account_id: UUID | None = None
    credential_mode: SurfaceCredentialMode | None = None
    config: SurfaceBehaviorConfigInput = Field(default_factory=SurfaceBehaviorConfigInput)
    is_enabled: bool | None = None

    model_config = ConfigDict(extra="forbid")


class AgentSurfaceResponse(BaseModel):
    id: UUID
    pod_id: UUID
    name: str
    agent_id: UUID | None = None
    agent_name: str | None = None
    uses_default_agent: bool = False
    platform: SurfacePlatform
    credential_mode: SurfaceCredentialMode = SurfaceCredentialMode.SYSTEM
    account_id: UUID | None = None
    surface_identity_id: str | None = None
    surface_identity_username: str | None = None
    webhook_url: str | None = None
    config: SurfaceConfigResponse
    status: AgentSurfaceStatus = AgentSurfaceStatus.ACTIVE

    model_config = ConfigDict(from_attributes=True)


class AgentSurfaceListResponse(BaseModel):
    items: list[AgentSurfaceResponse]
    limit: int
    next_page_token: str | None = None


class SurfaceSendRequest(BaseModel):
    """Send a proactive message to a pod member on this surface."""

    user_id: UUID = Field(..., description="Target pod member (Lemma user id).")
    message: str = Field(..., min_length=1, description="Message text to deliver.")


class SurfaceSendResponse(BaseModel):
    sent: bool


class SurfaceAdminConsentInfo(BaseModel):
    """Admin-consent state for surfaces that require an OAuth grant (Teams)."""

    required: bool = False
    granted: bool = False
    consent_url: str | None = None


class SurfaceSetupResponse(BaseModel):
    """Everything a caller needs to finish setting up a surface, in one read.

    Merges the former setup-status, admin-consent, and platform-checklist
    endpoints. Works both before a surface exists (`exists=False`, guide only)
    and after.

    ``ready`` is True when the user has nothing left to do (system credentials,
    or an already-granted consent). ``actions`` is populated *only* when the
    user must act — e.g. point their own Slack/Teams/WhatsApp app at Lemma —
    so the UI can show a clean "Ready" state otherwise.
    """

    platform: SurfacePlatform
    exists: bool
    status: AgentSurfaceStatus
    ready: bool = False
    webhook_url: str | None = None
    admin_consent: SurfaceAdminConsentInfo | None = None
    actions: list[SurfaceSetupAction] = Field(default_factory=list)
    guide: SurfacePlatformSetupGuide

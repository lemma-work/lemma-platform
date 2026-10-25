from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.setup_actions import SurfaceSetupAction
from app.modules.agent_surfaces.domain.setup_guides import SurfacePlatformSetupGuide
from app.modules.agent_surfaces.domain.surface_representation import (
    AgentSurfaceResponse,
    SurfaceBehaviorConfigInput,
)


class AvailableSurfaceChannelResponse(BaseModel):
    """A channel/group the surface bot can be configured to respond in."""

    id: str
    name: str | None = None
    is_member: bool | None = None


class AvailableSurfaceChannelsResponse(BaseModel):
    channels: list[AvailableSurfaceChannelResponse] = Field(default_factory=list)


class SurfaceUpdateRequest(BaseModel):
    """Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

    Partial update (merge semantics): only fields present in the request are
    applied. The surface's ``platform`` and ``name`` are immutable — delete and
    recreate to change either.
    """

    default_agent_name: str | None = None
    pod_connector: str | None = Field(
        default=None,
        description=(
            "Slug of the SHARED pod connector to run this surface as instead: "
            "the repair when the account it runs as is withdrawn or expires. "
            "Omit to keep the one it has."
        ),
    )
    account_id: UUID | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Deprecated: name `pod_connector` instead. Still accepted, as the "
            "pod connector that serves this account to you."
        ),
    )
    credential_mode: SurfaceCredentialMode | None = None
    config: SurfaceBehaviorConfigInput = Field(
        default_factory=SurfaceBehaviorConfigInput
    )
    is_enabled: bool | None = None

    model_config = ConfigDict(extra="forbid")


class TelegramManagedBotSetupRequest(BaseModel):
    name: str | None = Field(
        default=None,
        description="Pod-unique surface name. Defaults to telegram.",
    )
    default_agent_name: str | None = None
    config: SurfaceBehaviorConfigInput = Field(
        default_factory=SurfaceBehaviorConfigInput
    )
    is_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class TelegramManagedBotSetupResponse(BaseModel):
    setup_id: str
    status: str
    launch_url: str
    manager_bot_username: str
    expires_at: str
    pod_connector: str | None = None
    surface_id: UUID | None = None
    bot_username: str | None = None
    bot_launch_url: str | None = None
    error: str | None = None


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

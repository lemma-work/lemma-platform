from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.connectors.contracts import AuthScheme
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
    SurfaceSlackConfig,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
    NotificationStatus,
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
    """One channel's routing, in the same three states the domain models.

    ``use_pod_assistant`` is not a synonym for an absent ``agent_name`` — see
    :class:`SurfaceChannelRoute`. Omitting it here is what silently turned an
    explicit "the pod assistant answers here", picked from inside Slack, back
    into "unconfigured" on the next save from the web UI.
    """

    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None
    use_pod_assistant: bool = False

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_channel_ref(self) -> "SurfaceChannelRouteInput":
        if not self.channel_id and not self.channel_name:
            raise ValueError("channel_id or channel_name is required")
        if self.use_pod_assistant and self.agent_name:
            raise ValueError(
                "use_pod_assistant and agent_name are different answers; set one"
            )
        return self


class SurfaceSendPolicyConfig(BaseModel):
    """Proactive-send controls. Mirrored across request and response."""

    allow_send: bool = False


class SurfaceTelegramConfigInput(BaseModel):
    """Selects the pod app exposed as this bot's Telegram Mini App."""

    app_name: str | None = None

    model_config = ConfigDict(extra="forbid")


class SurfaceSlackConfigInput(BaseModel):
    """The Slack settings a *caller* owns.

    Only ``app_name``. The per-person DM agent map is written from inside Slack
    — each person picks their own in the App Home — so it is readable here and
    never writable, which keeps one editor from reassigning everybody.
    """

    app_name: str | None = None

    model_config = ConfigDict(extra="forbid")


class SurfaceSlackConfigResponse(BaseModel):
    """Slack settings as read back. ``dm_agent_by_user`` maps a Slack user id to
    the agent that person chose, or ``__pod_assistant__`` when they explicitly
    chose the pod assistant. A user absent from the map has never chosen and
    falls to the surface default."""

    app_name: str | None = None
    dm_agent_by_user: dict[str, str] = Field(default_factory=dict)


class SurfaceBehaviorConfigInput(BaseModel):
    identity: SurfaceIdentityConfigInput = Field(default_factory=SurfaceIdentityConfigInput)
    channels: list[SurfaceChannelRouteInput] = Field(default_factory=list)
    dm_conversation_reset_after_hours: int = 24
    send_policy: SurfaceSendPolicyConfig = Field(default_factory=SurfaceSendPolicyConfig)
    telegram: SurfaceTelegramConfigInput = Field(
        default_factory=SurfaceTelegramConfigInput
    )
    slack: SurfaceSlackConfigInput = Field(default_factory=SurfaceSlackConfigInput)

    model_config = ConfigDict(extra="forbid")


class SurfaceChannelRouteResponse(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None
    use_pod_assistant: bool = False


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
    telegram: SurfaceTelegramConfigInput = Field(
        default_factory=SurfaceTelegramConfigInput
    )
    slack: SurfaceSlackConfigResponse = Field(
        default_factory=SurfaceSlackConfigResponse
    )

    @classmethod
    def from_domain(cls, config: SurfaceConfig) -> "SurfaceConfigResponse":
        return cls.model_validate(config.model_dump(mode="json"))


def surface_config_from_input(
    config_input: SurfaceBehaviorConfigInput,
    *,
    channel_routes: list[SurfaceChannelRoute],
) -> SurfaceConfig:
    """Build the domain config from API input (channel routes pre-resolved
    from agent names by the controller).

    ``slack.dm_agent_by_user`` is deliberately absent: it is written from
    inside Slack and never carried on a create, which has no one's choices yet.
    """
    return SurfaceConfig(
        dm_conversation_reset_after_hours=config_input.dm_conversation_reset_after_hours,
        identity=SurfaceIdentityPolicy(
            allowed_domains=config_input.identity.allowed_domains,
            allowed_email_addresses=config_input.identity.allowed_email_addresses,
        ),
        channels=channel_routes,
        send_policy=SurfaceSendPolicy(allow_send=config_input.send_policy.allow_send),
        telegram=SurfaceTelegramConfig(app_name=config_input.telegram.app_name),
        slack=SurfaceSlackConfig(app_name=config_input.slack.app_name),
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


class TelegramManagedBotSetupRequest(BaseModel):
    name: str | None = Field(
        default=None,
        description="Pod-unique surface name. Defaults to telegram.",
    )
    default_agent_name: str | None = None
    config: SurfaceBehaviorConfigInput = Field(default_factory=SurfaceBehaviorConfigInput)
    is_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


class TelegramManagedBotSetupResponse(BaseModel):
    setup_id: str
    status: str
    launch_url: str
    manager_bot_username: str
    expires_at: str
    account_id: UUID | None = None
    surface_id: UUID | None = None
    bot_username: str | None = None
    bot_launch_url: str | None = None
    error: str | None = None


class SurfaceReach(BaseModel):
    """How a human reaches this surface.

    ``handle`` is the platform-native name a person types/sees to message the
    bot (Slack/Teams bot display name, Telegram ``@username``, WhatsApp phone,
    or the account/email for email surfaces). ``email`` is the surface's email
    address, when it has one.
    """

    handle: str | None = None
    email: str | None = None


class SurfaceConnectionStatus(StrEnum):
    """Health of the account a surface runs on.

    Mirrors ``AccountStatus`` and adds ``MISSING`` for a surface pointing at an
    account row that is no longer there. Whether the owner is still in the pod
    is deliberately *not* folded in here: a departed owner's token keeps working
    until it expires, so it is a separate fact (``connected_by.is_pod_member``),
    not a rung on this ladder.
    """

    CONNECTED = "CONNECTED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    DISCONNECTED = "DISCONNECTED"
    MISSING = "MISSING"


class SurfaceConnectionOwner(BaseModel):
    """The person whose connected account backs a surface."""

    user_id: UUID
    name: str | None = None
    email: str | None = None
    # False once they leave the pod: the surface keeps working, but no one left
    # here can re-authorize it — they have to rebind it to their own account.
    is_pod_member: bool = False
    is_you: bool = False


class SurfaceConnection(BaseModel):
    """Which account a surface runs on, and who connected it.

    Accounts are personal (``accounts.user_id``) while surfaces belong to the
    pod, so ``account_id`` alone answers nothing for a teammate — they cannot
    resolve an id they don't own. This block is the pod-visible *identity* of
    that account: enough for any editor to see who to ask, never the credential.
    """

    account_id: UUID
    connector_id: str
    # The account's own label — a bot @username, mailbox, or workspace.
    display_name: str | None = None
    status: SurfaceConnectionStatus = SurfaceConnectionStatus.CONNECTED
    connected_by: SurfaceConnectionOwner | None = None


class AgentSurfaceResponse(BaseModel):
    id: UUID
    pod_id: UUID
    name: str
    agent_id: UUID | None = None
    agent_name: str | None = None
    uses_default_agent: bool = False
    platform: SurfacePlatform
    credential_mode: SurfaceCredentialMode = SurfaceCredentialMode.SYSTEM
    # Kept alongside ``connection`` because the write path (and pod bundles)
    # address the account by id; ``connection`` is what a reader can act on.
    account_id: UUID | None = None
    connection: SurfaceConnection | None = None
    surface_identity_id: str | None = None
    surface_identity_username: str | None = None
    surface_identity_email: str | None = None
    webhook_url: str | None = None
    reach: SurfaceReach | None = None
    config: SurfaceConfigResponse
    status: AgentSurfaceStatus = AgentSurfaceStatus.ACTIVE

    model_config = ConfigDict(from_attributes=True)


class AgentSurfaceListResponse(BaseModel):
    items: list[AgentSurfaceResponse]
    limit: int
    next_page_token: str | None = None


class UserSurfaceItem(BaseModel):
    """One of the current user's surfaces (across any pod they belong to)."""

    id: UUID
    name: str
    pod_id: UUID
    platform: SurfacePlatform
    agent_id: UUID | None = None
    is_default: bool = False


class UserSurfacePlatformGroup(BaseModel):
    """All of a user's surfaces for one platform. ``conflict`` is true when more
    than one surface could answer them (they should pick a ``default``)."""

    platform: SurfacePlatform
    conflict: bool = False
    default_surface_id: UUID | None = None
    surfaces: list[UserSurfaceItem]


class UserSurfacesResponse(BaseModel):
    groups: list[UserSurfacePlatformGroup]


class SetDefaultSurfaceRequest(BaseModel):
    """Pick which surface answers this user for ``platform`` when several could."""

    platform: SurfacePlatform
    surface_id: UUID


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


class SurfaceConnectDescriptor(BaseModel):
    """What the frontend needs to render the "connect an account" (CUSTOM) flow
    for a surface's connector — a slim projection of the connector's LEMMA
    capability. ``system_oauth_available`` means the platform supplies the OAuth
    app so the user connects without registering their own (distinct from whether
    a fully-managed SYSTEM bot exists — that's ``supported_credential_modes``)."""

    auth_scheme: AuthScheme
    auth_config_schema: dict[str, Any] | None = None
    credential_schema: dict[str, Any] | None = None
    system_oauth_available: bool = False
    supports_org_custom_oauth: bool = False


class SurfaceSystemClaim(BaseModel):
    """Whether this org can still put the platform's Lemma-managed bot/number
    behind a surface.

    The shared identity is claimable exactly once per organization, so the setup
    UI can render the option as unavailable *before* the user commits instead of
    discovering it as a failed save. ``claimed_by_pod_id`` is the pod holding the
    claim — always a pod in the caller's own org, so linking to it leaks nothing
    they can't already see."""

    available: bool
    claimed_by_pod_id: UUID | None = None
    claimed_by_surface_name: str | None = None


class AvailableSurface(BaseModel):
    """One connectable surface platform. ``supported_credential_modes`` is the
    single source of truth for how it can be set up: ``[CUSTOM]`` means an account
    must be connected; ``[CUSTOM, SYSTEM]`` means a Lemma-managed bot can also run
    with no account. The frontend derives ``account_needed = SYSTEM not in modes``
    and ``system_bot_available = SYSTEM in modes``."""

    platform: SurfacePlatform
    connector_id: str
    kind: str
    title: str | None = None
    description: str | None = None
    icon: str | None = None
    supported_credential_modes: list[SurfaceCredentialMode]
    connector_available: bool = True
    connect: SurfaceConnectDescriptor | None = None
    # Only meaningful when SYSTEM is in supported_credential_modes; None means
    # the platform has no Lemma-managed identity to claim in the first place.
    system_claim: SurfaceSystemClaim | None = None
    # Whether this deployment can provision a dedicated bot for the user through
    # a manager bot (Telegram). Lets the setup UI offer that path only where it
    # actually works, instead of failing once the user has committed to it.
    managed_setup_available: bool = False
    # Email only: the domain every managed address is minted under. Published
    # because an agent's address is decided by ``email_address_allocation`` from
    # its own name and the pod's — everything except this domain, which is
    # deployment configuration. With it the builder can show someone the address
    # their agent is about to get, instead of promising one.
    email_domain: str | None = None


class AvailableSurfacesResponse(BaseModel):
    surfaces: list[AvailableSurface] = Field(default_factory=list)


class NotificationResponse(BaseModel):
    """One notification, shaped for the inbox that renders it.

    Carries enough to draw the row *and* decide what its action button does,
    without a second request: ``awaiting_response`` says whether to draw one at
    all, and ``responds_through_action`` says whether it opens a text box or the
    real form described by ``action``.
    """

    id: UUID
    pod_id: UUID
    title: str
    body: str
    origin_kind: NotificationOriginKind
    origin_id: UUID | None = None
    origin_conversation_id: UUID | None = None

    # Who asked. Both, because "the pod's bot" is not an answer a person can act
    # on and the human behind a run is the one they will reply to.
    actor_user_id: UUID | None = None
    actor_agent_id: UUID | None = None

    status: NotificationStatus
    delivery_status: NotificationDeliveryStatus
    expects_response: bool
    awaiting_response: bool
    responds_through_action: bool
    action: dict[str, Any] | None = None

    delivery_platform: str | None = None
    delivery_conversation_id: UUID | None = None
    # Why nobody could be reached, in words a person can act on ("they have not
    # messaged the bot yet"). Present only when delivery did not succeed.
    undeliverable_reason: str | None = None

    response_summary: str | None = None
    response_data: dict[str, Any] | None = None

    created_at: datetime
    expires_at: datetime | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    responded_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity: NotificationEntity) -> "NotificationResponse":
        return cls(
            id=entity.id,
            pod_id=entity.pod_id,
            title=entity.title,
            body=entity.body,
            origin_kind=entity.origin_kind,
            origin_id=entity.origin_id,
            origin_conversation_id=entity.origin_conversation_id,
            actor_user_id=entity.actor_user_id,
            actor_agent_id=entity.actor_agent_id,
            status=entity.status,
            delivery_status=entity.delivery_status,
            expects_response=entity.expects_response,
            awaiting_response=entity.awaiting_response,
            responds_through_action=entity.responds_through_action,
            action=entity.action,
            delivery_platform=entity.delivery_platform,
            delivery_conversation_id=entity.delivery_conversation_id,
            undeliverable_reason=entity.delivery_error,
            response_summary=entity.response_summary,
            response_data=entity.response_data,
            created_at=entity.created_at,
            expires_at=entity.expires_at,
            delivered_at=entity.delivered_at,
            read_at=entity.read_at,
            responded_at=entity.responded_at,
        )
        # NB: ``background_instruction`` is deliberately absent. It is addressed
        # to the agent handling the reply and carries the asker's private
        # framing; showing it to the recipient would leak how they are being
        # managed.


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse] = Field(default_factory=list)
    limit: int
    next_page_token: str | None = None


class NotificationUnreadCountResponse(BaseModel):
    # Keyed on read_at, not status: a badge that only clears when you *answer*
    # is a badge people learn to ignore.
    unread: int


class NotificationRespondRequest(BaseModel):
    summary: str = Field(min_length=1, description="The answer, in the person's words.")
    data: dict[str, Any] | None = Field(
        default=None, description="Optional structured payload alongside the answer."
    )

    model_config = ConfigDict(extra="forbid")


class NotifyMemberRequest(BaseModel):
    """Send a notification to one pod member."""

    recipient: str = Field(
        description="Pod member id, user id, or email address of the recipient."
    )
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    background_instruction: str | None = Field(
        default=None,
        description=(
            "Never shown to the recipient. Tells the agent that handles their "
            "reply what to do with it."
        ),
    )
    expects_response: bool = True
    expires_in_seconds: int | None = Field(default=None, gt=0)

    model_config = ConfigDict(extra="forbid")

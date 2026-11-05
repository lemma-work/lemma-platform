"""A surface as it is written down outside the process.

The HTTP API reads and writes this shape, and a pod bundle carries the same
one: an exported surface is the GET response, and an imported one is validated
as the create request. It lives apart from `api/` because the bundle reaches it
through this module's contracts, and the reach and connection a surface reports
are worked out in services -- neither of which should have to import the HTTP
layer to name what it returns.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceStatus,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceGroupPolicy,
    SurfaceIdentityPolicy,
    SurfacePlatform,
    SurfaceSendPolicy,
    SurfaceSlackConfig,
    SurfaceTelegramConfig,
)
from app.modules.agent_surfaces.domain.surface_config import (
    ContactAnswer,
    SurfaceContactPolicy,
    SurfaceWhatsAppConfig,
    whatsapp_template_name,
)


# Whom a bot answers beyond the pod's members. These two settings are one
# question -- the people outside the pod, in groups and in private chats -- and
# are mirrored across request and response.


class SurfaceGroupsConfig(BaseModel):
    """How the bot treats people outside the pod in its groups. Mirrored."""

    answers_outsiders: bool = Field(
        default=True,
        description=(
            "Answer people outside the pod in this bot's groups, from what the "
            "pod made Public. Off, the bot answers only the pod's members, "
            "whatever a group's own switch says."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class SurfaceContactsConfig(BaseModel):
    """Whom the bot answers in private chats beyond the pod's members. Mirrored."""

    answer: ContactAnswer = Field(
        default=ContactAnswer.OFF,
        description=(
            "`off`: members only. `known`: members and the pod's existing "
            "contacts. `anyone`: a stranger becomes a contact with their first "
            "message. Only a bot that is the pod's own (its own token, number "
            "or email address) answers contacts."
        ),
    )
    looked_after_by: UUID | None = Field(
        default=None,
        description=(
            "The member contacts' conversations belong to. Defaults to whoever "
            "turns contacts on; must be a member of the pod."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class SurfaceIdentityConfigInput(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class SurfaceChannelRouteInput(BaseModel):
    """One channel this surface's agent may be spoken to in.

    An allow-list entry. A surface belongs to exactly one agent, so a channel
    says *where*, never *who* — it used to name an agent, back when one bot
    could serve several.
    """

    channel_id: str | None = None
    channel_name: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_channel_ref(self) -> "SurfaceChannelRouteInput":
        if not self.channel_id and not self.channel_name:
            raise ValueError("channel_id or channel_name is required")
        return self


class SurfaceSendPolicyConfig(BaseModel):
    """Proactive-send controls. Mirrored across request and response."""

    allow_send: bool = False


class SurfaceTelegramConfigInput(BaseModel):
    """Selects the pod app exposed as this bot's Telegram Mini App."""

    app_name: str | None = None

    model_config = ConfigDict(extra="forbid")


class SurfaceSlackConfigInput(BaseModel):
    """The Slack settings a caller owns."""

    app_name: str | None = None

    model_config = ConfigDict(extra="forbid")


class SurfaceSlackConfigResponse(BaseModel):
    """Slack settings as read back."""

    app_name: str | None = None


class SurfaceWhatsAppConfigInput(BaseModel):
    """WhatsApp settings. Mirrored across request and response."""

    reengage_template: str | None = Field(
        default=None,
        description=(
            "An approved message template on this number's business account. "
            "Sent to a member whose 24-hour window has closed, holding the "
            "message itself until they reply. Unset, such a message is refused."
        ),
    )
    language: str = Field(default="en_US", description="The template's language code.")

    model_config = ConfigDict(extra="forbid")

    @field_validator("reengage_template")
    @classmethod
    def _template_name(cls, value: str | None) -> str | None:
        return whatsapp_template_name(value)


class SurfaceBehaviorConfigInput(BaseModel):
    identity: SurfaceIdentityConfigInput = Field(
        default_factory=SurfaceIdentityConfigInput
    )
    channels: list[SurfaceChannelRouteInput] = Field(default_factory=list)
    dm_conversation_reset_after_hours: int | None = Field(
        default=None,
        deprecated=True,
        description=(
            "Ignored. A private chat is one conversation per agent and person, "
            "however long it goes quiet. Still accepted so existing pod bundles "
            "and clients keep working."
        ),
    )
    send_policy: SurfaceSendPolicyConfig = Field(
        default_factory=SurfaceSendPolicyConfig
    )
    telegram: SurfaceTelegramConfigInput = Field(
        default_factory=SurfaceTelegramConfigInput
    )
    slack: SurfaceSlackConfigInput = Field(default_factory=SurfaceSlackConfigInput)
    whatsapp: SurfaceWhatsAppConfigInput = Field(
        default_factory=SurfaceWhatsAppConfigInput
    )
    groups: SurfaceGroupsConfig = Field(default_factory=SurfaceGroupsConfig)
    contacts: SurfaceContactsConfig = Field(default_factory=SurfaceContactsConfig)

    model_config = ConfigDict(extra="forbid")


class SurfaceChannelRouteResponse(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None


class SurfaceIdentityConfigResponse(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)


class SurfaceConfigResponse(BaseModel):
    """Mirrors SurfaceBehaviorConfigInput: what you send is what you get back."""

    identity: SurfaceIdentityConfigResponse = Field(
        default_factory=SurfaceIdentityConfigResponse
    )
    channels: list[SurfaceChannelRouteResponse] = Field(default_factory=list)
    send_policy: SurfaceSendPolicyConfig = Field(
        default_factory=SurfaceSendPolicyConfig
    )
    telegram: SurfaceTelegramConfigInput = Field(
        default_factory=SurfaceTelegramConfigInput
    )
    slack: SurfaceSlackConfigResponse = Field(
        default_factory=SurfaceSlackConfigResponse
    )
    whatsapp: SurfaceWhatsAppConfigInput = Field(
        default_factory=SurfaceWhatsAppConfigInput
    )
    groups: SurfaceGroupsConfig = Field(default_factory=SurfaceGroupsConfig)
    contacts: SurfaceContactsConfig = Field(default_factory=SurfaceContactsConfig)

    @classmethod
    def from_domain(cls, config: SurfaceConfig) -> "SurfaceConfigResponse":
        return cls.model_validate(config.model_dump(mode="json"))


def surface_config_from_input(
    config_input: SurfaceBehaviorConfigInput,
    *,
    channel_routes: list[SurfaceChannelRoute],
) -> SurfaceConfig:
    """Build the domain config from API input."""
    return SurfaceConfig(
        identity=SurfaceIdentityPolicy(
            allowed_domains=config_input.identity.allowed_domains,
            allowed_email_addresses=config_input.identity.allowed_email_addresses,
        ),
        channels=channel_routes,
        send_policy=SurfaceSendPolicy(allow_send=config_input.send_policy.allow_send),
        telegram=SurfaceTelegramConfig(app_name=config_input.telegram.app_name),
        slack=SurfaceSlackConfig(app_name=config_input.slack.app_name),
        whatsapp=SurfaceWhatsAppConfig(
            reengage_template=config_input.whatsapp.reengage_template,
            language=config_input.whatsapp.language,
        ),
        groups=SurfaceGroupPolicy(
            answers_outsiders=config_input.groups.answers_outsiders
        ),
        contacts=SurfaceContactPolicy(
            answer=config_input.contacts.answer,
            looked_after_by=config_input.contacts.looked_after_by,
        ),
    )


class SurfaceCreateRequest(BaseModel):
    """Body for `POST /pods/{pod_id}/surfaces` — creates one surface.

    A pod may have several surfaces of the same ``platform`` — one bot per
    agent is the model, not the exception; ``name`` is the stable,
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
    pod_connector: str | None = Field(
        default=None,
        description=(
            "Slug of the SHARED pod connector this surface runs as: an account "
            "its owner shared with the pod. Omit to run on Lemma's own "
            "credentials where the platform has them."
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
    credential_mode: SurfaceCredentialMode = SurfaceCredentialMode.SYSTEM
    config: SurfaceBehaviorConfigInput = Field(
        default_factory=SurfaceBehaviorConfigInput
    )
    is_enabled: bool = True

    model_config = ConfigDict(extra="forbid")


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

    Mirrors ``AccountStatus`` and adds ``MISSING`` for a surface whose pod
    connector runs as no account right now: removed, or its account withdrawn
    by its owner or gone. Whether the owner is still in the pod
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
    """Which account a surface's pod connector runs as, and whose it is.

    Accounts are personal (``accounts.user_id``) while surfaces belong to the
    pod, so an account id alone answers nothing for a teammate — they cannot
    resolve an id they don't own. This block is the pod-visible *identity* of
    that account: enough for any editor to see who to ask, never the credential.
    """

    pod_connector: str
    #: ``None`` when the connector runs as no account right now (``MISSING``).
    account_id: UUID | None = None
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
    #: The pod connector the surface runs as, by slug -- what the write path
    #: and pod bundles address. ``connection`` says which account that is now.
    pod_connector: str | None = None
    connection: SurfaceConnection | None = None
    surface_identity_id: str | None = None
    surface_identity_username: str | None = None
    surface_identity_email: str | None = None
    webhook_url: str | None = None
    reach: SurfaceReach | None = None
    config: SurfaceConfigResponse
    status: AgentSurfaceStatus = AgentSurfaceStatus.ACTIVE
    #: Set while nobody in the space looks after the contacts this bot answers.
    contacts_warning: str | None = None
    #: Why the surface cannot do its work: the platform refused its webhook
    #: (``ERROR``) or its credentials. Absent while it works.
    problem: str | None = None

    model_config = ConfigDict(from_attributes=True)

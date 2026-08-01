from __future__ import annotations

from enum import StrEnum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.domain.aggregate import AggregateRoot
from app.core.domain.entity import Entity
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)


class ConversationType(StrEnum):
    EXTERNAL_DM = "EXTERNAL_DM"
    EXTERNAL_GROUP = "EXTERNAL_GROUP"

    @classmethod
    def _missing_(cls, value: object) -> "ConversationType | None":
        # Replayed last_event JSON blobs may carry the old lowercase value.
        if not isinstance(value, str):
            return None
        normalized = value.strip().upper()
        for member in cls:
            if member.value == normalized:
                return member
        return None


class SurfaceMode(StrEnum):
    DM = "DM"
    EMAIL = "EMAIL"


class SurfaceEventMode(StrEnum):
    WEBHOOK = "WEBHOOK"
    COMPOSIO_TRIGGER = "COMPOSIO_TRIGGER"


class SurfaceCredentialMode(StrEnum):
    SYSTEM = "SYSTEM"
    CUSTOM = "CUSTOM"


class SurfacePlatform(StrEnum):
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    GMAIL = "GMAIL"
    OUTLOOK = "OUTLOOK"
    RESEND = "RESEND"

    @classmethod
    def from_source(cls, source: str) -> "SurfacePlatform | None":
        try:
            return cls(str(source).upper())
        except ValueError:
            return None

    @property
    def is_email(self) -> bool:
        return self in {
            SurfacePlatform.GMAIL,
            SurfacePlatform.OUTLOOK,
            SurfacePlatform.RESEND,
        }


class SurfaceIdentityPolicy(BaseModel):
    """Restricts which resolved senders may use the surface (empty = everyone)."""

    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains", "allowed_email_addresses")
    @classmethod
    def _normalize(cls, values: list[str]) -> list[str]:
        return [value.strip().lower() for value in values if str(value).strip()]

    def allows_email(self, email: str | None) -> bool:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return True
        if not self.allowed_email_addresses and not self.allowed_domains:
            return True
        domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else ""
        return (
            normalized in self.allowed_email_addresses
            or domain in self.allowed_domains
        )


class SurfaceChannelRoute(BaseModel):
    """Routes one platform channel to an agent (by pod-unique agent name;
    None → the surface default agent). A route existing means it is active —
    remove it to stop routing the channel."""

    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None

    def matches(self, *, channel_id: str, channel_name: str) -> bool:
        route_channel_id = str(self.channel_id or "").strip()
        if channel_id and route_channel_id and route_channel_id == channel_id:
            return True
        route_channel_name = str(self.channel_name or "").strip().lower()
        return bool(
            channel_name and route_channel_name and route_channel_name == channel_name
        )


class SendAudience(StrEnum):
    """Who an agent on this surface is allowed to reach unprompted."""

    # Nothing proactive at all. The default, because it is what every surface
    # created before this field existed already did — a new capability must not
    # switch itself on for surfaces nobody has revisited.
    NOBODY = "NOBODY"
    # Only the person whose conversation the agent is already in. Telling
    # someone about work they asked for is not the same act as putting words in
    # front of a colleague, so the two are separate rungs.
    SELF = "SELF"
    # Any member of the surface's pod.
    POD_MEMBERS = "POD_MEMBERS"


class SurfaceSendPolicy(BaseModel):
    """Controls proactive sending for a surface.

    An agent that can message any pod member is a real capability and a real
    hazard: the recipient sees the pod's bot, not "the agent someone else's
    schedule is running", and will extend the bot the trust they extend to
    Lemma. So the audience is explicit, attribution is mandatory rather than
    optional, and there is a ceiling on how often one agent can reach one person.
    """

    audience: SendAudience = SendAudience.NOBODY
    # Ceiling per (agent, recipient) per hour. A badly-prompted agent in a retry
    # loop is the expected failure, not a malicious one, and there is no circuit
    # breaker on the send path the way there is on schedules.
    max_messages_per_recipient_per_hour: int = 6

    # Deprecated: the original single boolean, kept readable for one release so
    # existing surface rows keep working. ``allow_send=True`` meant "expose the
    # current-user send tool", which is exactly SELF.
    allow_send: bool | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _adopt_legacy_allow_send(self) -> "SurfaceSendPolicy":
        # Only let the legacy flag speak when the new field was left at its
        # default, so an explicit audience always wins over a stale boolean.
        if self.allow_send is not None and "audience" not in self.model_fields_set:
            object.__setattr__(
                self,
                "audience",
                SendAudience.SELF if self.allow_send else SendAudience.NOBODY,
            )
        return self

    @property
    def allows_self(self) -> bool:
        return self.audience in {SendAudience.SELF, SendAudience.POD_MEMBERS}

    @property
    def allows_pod_members(self) -> bool:
        return self.audience is SendAudience.POD_MEMBERS


class SurfaceTelegramConfig(BaseModel):
    """Telegram-only presentation settings for a surface."""

    app_name: str | None = None


class SurfaceConfig(BaseModel):
    """User-editable surface behavior. Exactly what the API accepts and returns.

    Derived/identity data (workspace ids, secrets, schedule links) lives in
    dedicated entity fields, never in here.
    """

    dm_conversation_reset_after_hours: int = 24
    identity: SurfaceIdentityPolicy = Field(default_factory=SurfaceIdentityPolicy)
    channels: list[SurfaceChannelRoute] = Field(default_factory=list)
    send_policy: SurfaceSendPolicy = Field(default_factory=SurfaceSendPolicy)
    telegram: SurfaceTelegramConfig = Field(default_factory=SurfaceTelegramConfig)


class ExternalSurfaceUserEntity(Entity):
    platform: str
    tenant_id: str | None = None
    external_user_id: str
    email: str | None = None
    phone: str | None = None
    display_name: str | None = None
    raw_profile: dict[str, Any] = Field(default_factory=dict)
    resolved_user_id: UUID | None = None
    last_seen_at: datetime | None = None


class ReachKind(StrEnum):
    """Where a person can be reached.

    ``APP`` is Lemma itself and is deliberately not a :class:`SurfacePlatform`:
    the web app is not a third-party bot install, it has no credentials, no
    webhook and no external identity. Every other value mirrors a platform.
    """

    APP = "APP"
    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    GMAIL = "GMAIL"
    OUTLOOK = "OUTLOOK"
    RESEND = "RESEND"

    @property
    def is_app(self) -> bool:
        return self is ReachKind.APP

    @classmethod
    def for_platform(cls, platform: SurfacePlatform) -> "ReachKind":
        return cls(platform.value)


class ReachStatus(StrEnum):
    ACTIVE = "ACTIVE"
    # The identity behind this reach was invalidated (e.g. a profile phone
    # change clears the cached platform identity). Kept, not deleted, so
    # "we used to be able to reach you here" stays answerable.
    STALE = "STALE"
    # The platform rejected delivery in a way that will not self-heal.
    BLOCKED = "BLOCKED"


class MemberReach(Entity):
    """How this pod can reach one person on one channel.

    Today this fact is implied by a three-way join — the cached platform
    identity, the conversation link, and the ``last_event`` blob inside it — and
    is only discoverable per-surface, after the person has spoken first. As a row
    it becomes answerable up front: *can we reach Deepak at all, and where?*

    Pod-scoped on purpose. ``AgentSurfaceExternalUser`` is correctly cross-pod
    (one Telegram account, many pods); conflating the two is how a shared bot
    cross-posts between organizations.
    """

    pod_id: UUID
    user_id: UUID
    kind: ReachKind
    # None for APP, which has no surface behind it.
    surface_id: UUID | None = None
    # Denormalized from the identity cache so clearing ``resolved_user_id``
    # (a profile phone change) degrades to STALE rather than silently orphaning.
    external_user_id: str | None = None
    target: SurfaceTarget | None = None
    status: ReachStatus = ReachStatus.ACTIVE
    last_inbound_at: datetime | None = None
    # WhatsApp's 24h customer-service window, and anything like it. Held as data
    # rather than recomputed in code at every send site.
    window_expires_at: datetime | None = None
    opted_out_at: datetime | None = None

    @property
    def is_opted_out(self) -> bool:
        return self.opted_out_at is not None

    def is_deliverable(self, *, now: datetime | None = None) -> bool:
        """Whether a message sent right now would be allowed to land.

        The APP reach is always deliverable — that is its whole job. It is the
        one channel that cannot 403, expire, or be muted out of existence, which
        is what makes it a safe fallback.
        """
        if self.is_opted_out or self.status is not ReachStatus.ACTIVE:
            return False
        if self.kind.is_app:
            return True
        if self.target is None:
            return False
        if self.window_expires_at is not None:
            moment = now or datetime.now(timezone.utc)
            expires = self.window_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= moment:
                return False
        return True


class NotificationOrigin(StrEnum):
    """What produced a notification. Answers "why am I being told this?"."""

    SCHEDULE_RUN = "SCHEDULE_RUN"
    WORKFLOW_RUN = "WORKFLOW_RUN"
    AGENT_RUN = "AGENT_RUN"


class Notification(Entity):
    """One thing a person is being told, in Lemma itself.

    Written on every ``notify`` regardless of whether a chat platform also got
    it, because the app reach is the only one that cannot fail — which makes it
    both the fallback and the durable record of what was sent.

    Unread state lives here rather than being derived from conversations:
    deciding "which conversations count as notifications" by reading
    ``conversation_metadata`` is exactly the ambiguity this row exists to remove.
    """

    pod_id: UUID
    user_id: UUID
    # The conversation this notification belongs to — where clicking it lands,
    # and where a reply continues. None only for notifications with no thread
    # behind them.
    conversation_id: UUID | None = None
    agent_id: UUID | None = None
    title: str | None = None
    body: str
    origin_type: NotificationOrigin | None = None
    origin_id: UUID | None = None
    read_at: datetime | None = None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None


class ParsedInboundSurfaceEvent(BaseModel):
    platform: SurfacePlatform
    conversation_type: ConversationType
    tenant_id: str | None = None
    external_channel_id: str | None = None
    external_thread_id: str
    external_message_id: str | None = None
    sender_external_user_id: str | None = None
    sender_aad_object_id: str | None = None
    sender_email: str | None = None
    sender_phone: str | None = None
    sender_display_name: str | None = None
    message_text: str
    is_dm: bool = False
    mentioned_agent: bool = False
    should_start_conversation: bool = True
    reply_target: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    def is_group_conversation(self) -> bool:
        return self.conversation_type == ConversationType.EXTERNAL_GROUP

    def reply_recipient_id(self) -> str | None:
        return (
            self.reply_target.get("channel")
            or self.reply_target.get("chat_id")
            or self.external_channel_id
        )

    def to_target(self) -> "SurfaceTarget":
        """Project this event down to the address a reply would go to."""
        return SurfaceTarget(
            platform=self.platform,
            reply_target=dict(self.reply_target),
            external_channel_id=self.external_channel_id,
            external_thread_id=self.external_thread_id,
            external_message_id=self.external_message_id,
            sender_external_user_id=self.sender_external_user_id,
            sender_display_name=self.sender_display_name,
            sender_email=self.sender_email,
            sender_phone=self.sender_phone,
            is_dm=self.is_dm,
            metadata=dict(self.metadata),
        )


class SurfaceTarget(BaseModel):
    """Where a message goes — the addressable half of a surface conversation.

    Egress has historically been "reply to the last inbound event": every adapter
    takes a :class:`ParsedInboundSurfaceEvent` and reads a handful of routing
    fields off it. That makes proactive delivery impossible, because a message
    the *agent* starts has no inbound event behind it.

    This type is that handful of fields, extracted and stored in its own right —
    a durable address for one person on one surface. ``reply_target`` and
    ``metadata`` are carried whole rather than key-by-key: they are already JSONB
    on the wire, they are small, and projecting them field-by-field would silently
    drop whatever a platform starts depending on next.

    ``raw_payload`` is deliberately absent. The only consumer is Outlook's
    ``requires_message_fetch`` enrichment, which is an inbound concern, and it can
    be large.
    """

    platform: SurfacePlatform
    reply_target: dict[str, Any] = Field(default_factory=dict)
    external_channel_id: str | None = None
    external_thread_id: str
    external_message_id: str | None = None
    sender_external_user_id: str | None = None
    sender_display_name: str | None = None
    sender_email: str | None = None
    sender_phone: str | None = None
    is_dm: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_event(self) -> "ParsedInboundSurfaceEvent":
        """Rebuild an egress-shaped event for the platform adapters.

        Adapters still take an event; until that signature moves, this is the
        seam. The inbound-only fields (``message_text``, ``raw_payload``,
        ``mentioned_agent``) are empty on purpose — no send path reads them, and
        anything that starts to is a bug worth failing on rather than feeding a
        plausible-looking blank.
        """
        return ParsedInboundSurfaceEvent(
            platform=self.platform,
            conversation_type=(
                ConversationType.EXTERNAL_DM
                if self.is_dm
                else ConversationType.EXTERNAL_GROUP
            ),
            external_channel_id=self.external_channel_id,
            external_thread_id=self.external_thread_id,
            external_message_id=self.external_message_id,
            sender_external_user_id=self.sender_external_user_id,
            sender_email=self.sender_email,
            sender_phone=self.sender_phone,
            sender_display_name=self.sender_display_name,
            message_text="",
            is_dm=self.is_dm,
            reply_target=dict(self.reply_target),
            metadata=dict(self.metadata),
        )


class ParsedSurfaceInteraction(BaseModel):
    """A native-form submission (Slack block_actions / Teams Action.Submit).

    For prompts, ``callback_id`` encodes ``conversation_id|tool_call_id`` (see
    ``display_resource_renderer.parse_callback_id``). Conversation-level actions
    resolve the current conversation through the durable surface/thread link
    instead of carrying another copy of its id. ``values`` holds the collected
    field name → value map; ``dedup_id`` uniquely identifies this submission for
    replay protection.
    """

    platform: SurfacePlatform
    tenant_id: str | None = None
    external_channel_id: str | None = None
    external_thread_id: str | None = None
    external_user_id: str | None = None
    callback_id: str = ""
    action: str | None = None
    interaction_state: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    # Set when the tapped control is a native approval button; carries the
    # canonical AgentRunApprovalDecision value (APPROVE_ONCE / DENY /
    # APPROVE_FOR_SESSION). None means this is an ask_user answer submission.
    approval_decision: str | None = None
    reply_target: dict[str, Any] = Field(default_factory=dict)
    dedup_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ResolvedSurfaceUser(BaseModel):
    internal_user_id: UUID | None = None
    external_user_id: str | None = None
    email: str | None = None
    phone: str | None = None
    display_name: str | None = None


class AgentSurfaceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PENDING_ADMIN_CONSENT = "PENDING_ADMIN_CONSENT"
    INACTIVE = "INACTIVE"
    NEEDS_SETUP = "NEEDS_SETUP"
    ERROR = "ERROR"

    def accepts_inbound_events(self) -> bool:
        return self is AgentSurfaceStatus.ACTIVE


class AgentSurfaceEntity(AggregateRoot):
    pod_id: UUID
    # Stable, pod-unique identifier used by the API (like agent names). Defaults
    # to the lowercased platform when not given; a pod may have several surfaces
    # of the same platform (different bots/agents), each with its own name.
    name: str
    agent_id: UUID | None = None
    surface_type: SurfacePlatform
    mode: SurfaceMode = SurfaceMode.DM
    event_mode: SurfaceEventMode = SurfaceEventMode.WEBHOOK
    credential_mode: SurfaceCredentialMode = SurfaceCredentialMode.SYSTEM
    config: SurfaceConfig
    # Entity-level routing/derived fields (stored as dedicated DB columns)
    account_id: UUID | None = None
    external_workspace_id: str | None = None
    external_tenant_id: str | None = None
    external_channel_id: str | None = None
    surface_identity_id: str | None = None
    surface_identity_username: str | None = None
    schedule_id: UUID | None = None       # Gmail/Outlook: linked email schedule
    surface_identity_email: str | None = None    # Gmail/Outlook: for self-email filtering
    webhook_secret: str | None = None
    status: AgentSurfaceStatus = AgentSurfaceStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status is not AgentSurfaceStatus.INACTIVE

    @staticmethod
    def default_name_for(surface_type: SurfacePlatform) -> str:
        return surface_type.value.lower()

    @classmethod
    def create(
        cls,
        *,
        pod_id: UUID,
        surface_type: str | SurfacePlatform,
        name: str | None = None,
        config: SurfaceConfig | None = None,
        agent_id: UUID | None = None,
        mode: SurfaceMode | None = None,
        event_mode: SurfaceEventMode | None = None,
        credential_mode: SurfaceCredentialMode | None = None,
        account_id: UUID | None = None,
        external_workspace_id: str | None = None,
        external_tenant_id: str | None = None,
        external_channel_id: str | None = None,
        surface_identity_id: str | None = None,
    ) -> "AgentSurfaceEntity":
        resolved = SurfacePlatform(str(surface_type).upper())
        resolved_name = (name or "").strip() or cls.default_name_for(resolved)
        config = config if config is not None else SurfaceConfig()
        resolved_mode = cls._resolve_mode(resolved, mode)
        resolved_event_mode = cls._default_event_mode(resolved, event_mode)
        cls._validate_binding(
            surface_type=resolved,
            mode=resolved_mode,
            event_mode=resolved_event_mode,
            account_id=account_id,
        )

        initial_status = (
            AgentSurfaceStatus.PENDING_ADMIN_CONSENT
            if resolved is SurfacePlatform.TEAMS
            else AgentSurfaceStatus.ACTIVE
        )

        return cls(
            pod_id=pod_id,
            name=resolved_name,
            agent_id=agent_id,
            surface_type=resolved,
            mode=resolved_mode,
            event_mode=resolved_event_mode,
            credential_mode=credential_mode
            or (
                SurfaceCredentialMode.CUSTOM
                if account_id is not None
                else SurfaceCredentialMode.SYSTEM
            ),
            config=config,
            account_id=account_id,
            external_workspace_id=external_workspace_id,
            external_tenant_id=external_tenant_id,
            external_channel_id=external_channel_id,
            surface_identity_id=surface_identity_id,
            webhook_secret=None,
            status=initial_status,
        )

    @staticmethod
    def _resolve_mode(
        surface_type: SurfacePlatform,
        mode: SurfaceMode | str | None,
    ) -> SurfaceMode:
        if mode is not None:
            return SurfaceMode(mode.value if isinstance(mode, SurfaceMode) else mode)
        return SurfaceMode.EMAIL if surface_type.is_email else SurfaceMode.DM

    @staticmethod
    def _validate_binding(
        *,
        surface_type: SurfacePlatform,
        mode: SurfaceMode,
        event_mode: SurfaceEventMode,
        account_id: UUID | None,
    ) -> None:
        if mode is SurfaceMode.EMAIL and not surface_type.is_email:
            raise AgentSurfaceValidationError("EMAIL mode is only supported for Gmail and Outlook")
        # Resend is an email surface delivered over a native webhook (system
        # creds), not a Composio trigger like Gmail/Outlook.
        if (
            mode is SurfaceMode.EMAIL
            and surface_type is not SurfacePlatform.RESEND
            and event_mode is not SurfaceEventMode.COMPOSIO_TRIGGER
        ):
            raise AgentSurfaceValidationError(
                "EMAIL surfaces require COMPOSIO_TRIGGER event_mode"
            )
        if mode is not SurfaceMode.EMAIL and event_mode is SurfaceEventMode.COMPOSIO_TRIGGER:
            raise AgentSurfaceValidationError(
                "COMPOSIO_TRIGGER event_mode is only supported for EMAIL surfaces"
            )
        if surface_type in {
            SurfacePlatform.SLACK,
            SurfacePlatform.TEAMS,
            SurfacePlatform.GMAIL,
            SurfacePlatform.OUTLOOK,
        } and account_id is None:
            raise AgentSurfaceValidationError(
                f"{surface_type.value} surfaces require account_id"
            )

    @staticmethod
    def _default_event_mode(
        surface_type: SurfacePlatform,
        event_mode: SurfaceEventMode | str | None,
    ) -> SurfaceEventMode:
        if event_mode is not None:
            return SurfaceEventMode(
                event_mode.value
                if isinstance(event_mode, SurfaceEventMode)
                else event_mode
            )
        # Resend uses a native inbound webhook; Gmail/Outlook use Composio triggers.
        if surface_type is SurfacePlatform.RESEND:
            return SurfaceEventMode.WEBHOOK
        if surface_type.is_email:
            return SurfaceEventMode.COMPOSIO_TRIGGER
        return SurfaceEventMode.WEBHOOK

    def activate(self) -> None:
        self.status = AgentSurfaceStatus.ACTIVE
        self.updated_at = datetime.now(timezone.utc)

    def configure_webhook_secret(self, *, secret: str) -> None:
        self.webhook_secret = secret
        self.updated_at = datetime.now(timezone.utc)

    def update_config(
        self,
        config: SurfaceConfig,
        *,
        account_id: UUID | None = None,
        mode: SurfaceMode | None = None,
        event_mode: SurfaceEventMode | None = None,
        credential_mode: SurfaceCredentialMode | None = None,
        external_workspace_id: str | None = None,
        external_tenant_id: str | None = None,
        external_channel_id: str | None = None,
        surface_identity_id: str | None = None,
    ) -> None:
        next_mode = self._resolve_mode(self.surface_type, mode) if mode is not None else self.mode
        next_event_mode = (
            self._default_event_mode(self.surface_type, event_mode)
            if event_mode is not None
            else self.event_mode
        )
        next_account_id = account_id if account_id is not None else self.account_id
        self._validate_binding(
            surface_type=self.surface_type,
            mode=next_mode,
            event_mode=next_event_mode,
            account_id=next_account_id,
        )
        self.config = config
        self.mode = next_mode
        self.event_mode = next_event_mode
        if credential_mode is not None:
            self.credential_mode = credential_mode
        self.account_id = next_account_id
        if external_workspace_id is not None:
            self.external_workspace_id = external_workspace_id
        if external_tenant_id is not None:
            self.external_tenant_id = external_tenant_id
        if external_channel_id is not None:
            self.external_channel_id = external_channel_id
        if surface_identity_id is not None:
            self.surface_identity_id = surface_identity_id
        self.updated_at = datetime.now(timezone.utc)

    def toggle_active(self, is_active: bool) -> None:
        self.status = (
            AgentSurfaceStatus.ACTIVE
            if is_active
            else AgentSurfaceStatus.INACTIVE
        )
        self.updated_at = datetime.now(timezone.utc)

    def update_agent(self, agent_id: UUID | None) -> None:
        self.agent_id = agent_id
        self.updated_at = datetime.now(timezone.utc)

    def matches_platform(self, platform: str) -> bool:
        return self.surface_type.value == str(platform).upper()

    def matches_tenant(self, tenant_id: str | None) -> bool:
        if self.surface_type is SurfacePlatform.TEAMS:
            expected = self.external_tenant_id
            return not expected or not tenant_id or expected == tenant_id
        if self.surface_type is SurfacePlatform.SLACK:
            expected = self.external_workspace_id
            return not expected or not tenant_id or expected == tenant_id
        return True

    def should_ignore_sender(self, sender_external_user_id: str | None) -> bool:
        if self.surface_type is SurfacePlatform.SLACK:
            return bool(
                sender_external_user_id
                and self.surface_identity_id == sender_external_user_id
            )
        return False

    def matches_channel(self, channel_id: str | None) -> bool:
        if not channel_id:
            return False
        if self.external_channel_id and self.external_channel_id == channel_id:
            return True
        return self.channel_route_for(channel_id=channel_id) is not None

    def channel_route_for(
        self,
        *,
        channel_id: str | None = None,
        channel_name: str | None = None,
    ) -> SurfaceChannelRoute | None:
        normalized_id = str(channel_id or "").strip()
        normalized_name = str(channel_name or "").strip().lower()
        for route in self.config.channels:
            if route.matches(channel_id=normalized_id, channel_name=normalized_name):
                return route
        return None

    def allows_inbound_event(self, event: ParsedInboundSurfaceEvent) -> bool:
        if not self.status.accepts_inbound_events():
            return False
        if not self.matches_platform(event.platform):
            return False
        if not self.matches_tenant(event.tenant_id):
            return False
        if self.mode is SurfaceMode.EMAIL:
            return event.should_start_conversation
        if event.is_dm:
            return True
        # Slack/Teams gate channel access by a configured channel route. Telegram
        # groups have no allow-list — being added to the group is the
        # authorization — so any group is accepted (the @mention gate below, plus
        # the pod-membership check on the sender, still apply).
        if self.surface_type is not SurfacePlatform.TELEGRAM and not self.matches_channel(
            event.external_channel_id
        ):
            return False
        # Channels and groups (Slack channels, Teams channels, Telegram groups):
        # respond ONLY when the bot is @mentioned, or when the user is replying
        # within an existing bot thread. There is no per-channel opt-out — being
        # mentioned is the universal trigger.
        if event.metadata.get("is_thread_reply"):
            return True
        if not event.mentioned_agent:
            return False
        if self.surface_type is SurfacePlatform.SLACK:
            # Slack fires app_mention for any @mention in the channel; make sure
            # it was THIS bot that was mentioned.
            mentioned_user_ids = set(event.metadata.get("mentioned_user_ids") or [])
            bot_user_id = self.surface_identity_id
            return (
                not bot_user_id
                or not mentioned_user_ids
                or bot_user_id in mentioned_user_ids
            )
        return True


class AgentSurfaceConversationLink(Entity):
    surface_id: UUID
    conversation_id: UUID
    platform: str
    external_channel_id: str | None = None
    external_thread_id: str
    external_user_id: str | None = None
    routed_agent_id: UUID | None = None
    conversation_kind: str = "DM"
    route_key: str | None = None
    last_event: dict[str, Any] = Field(default_factory=dict)
    last_message_id: str | None = None
    # Last time the *person* wrote here. ``updated_at`` is not a substitute:
    # proactive sends bump the row too.
    last_inbound_at: datetime | None = None

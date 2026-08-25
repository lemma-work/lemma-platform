from __future__ import annotations

from enum import StrEnum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.domain.aggregate import AggregateRoot
from app.core.domain.entity import Entity
from app.modules.agent_surfaces.domain.events import SurfaceConnectedEvent
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
)
from app.modules.agent_surfaces.domain.surface_config import (
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceIdentityPolicy,
    SurfaceSendPolicy,
    SurfaceSlackConfig,
    SurfaceTelegramConfig,
)

__all__ = [
    "SurfaceChannelRoute",
    "SurfaceConfig",
    "SurfaceIdentityPolicy",
    "SurfaceSendPolicy",
    "SurfaceSlackConfig",
    "SurfaceTelegramConfig",
]


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


class ThreadShape(StrEnum):
    """Does one thread id carry many conversations, or exactly one?

    The axis the conversation reset actually turns on, and it is a property of
    the *conversation*, not of the surface. One Slack install has both shapes at
    once, which is why asking ``surface.mode`` got it wrong: a channel thread
    inherited the DM reset and a reply a day later started a fresh conversation
    with no history -- while Slack showed the person the whole thread.
    """

    #: A chat DM. One permanent thread id carries every conversation you will
    #: ever have there, so something has to cut it into conversations.
    MULTIPLEXED = "MULTIPLEXED"
    #: A channel thread, an email thread. The platform already bounded it to one
    #: topic, so a time-based reset would cut a conversation that is still one.
    TOPIC_SCOPED = "TOPIC_SCOPED"


def thread_shape(conversation_kind: str | None) -> ThreadShape:
    """The shape of the thread a conversation of this kind lives in.

    Derived rather than stored: ``conversation_kind`` is already on every link
    (``NOT NULL``, defaulting to ``DM``), so there is nothing to migrate and a
    row written before routing set it degrades to the reset that was already
    happening -- no change, rather than a new behaviour.
    """
    return (
        ThreadShape.MULTIPLEXED
        if str(conversation_kind or "DM").upper() == "DM"
        else ThreadShape.TOPIC_SCOPED
    )


class SurfaceMode(StrEnum):
    DM = "DM"
    EMAIL = "EMAIL"


class SurfaceEventMode(StrEnum):
    """How a surface receives. Only one way now, and the member is kept because
    the column stores it -- ``COMPOSIO_TRIGGER`` went with the polled mailboxes."""

    WEBHOOK = "WEBHOOK"


class SurfaceCredentialMode(StrEnum):
    SYSTEM = "SYSTEM"
    CUSTOM = "CUSTOM"


class SurfacePlatform(StrEnum):
    """The platforms a pod can be reached on.

    Email is Resend, and only Resend. Gmail and Outlook were here as
    Composio-backed mailboxes, which made "an email surface" mean three
    different transports with three attachment strategies between them -- bytes,
    Graph drafts, and a signed URL the provider downloads server-side. Reaching
    a Gmail *account* is still something an agent does, through the connector;
    it is just not a surface.
    """

    SLACK = "SLACK"
    TEAMS = "TEAMS"
    WHATSAPP = "WHATSAPP"
    TELEGRAM = "TELEGRAM"
    RESEND = "RESEND"

    @classmethod
    def from_source(cls, source: str) -> "SurfacePlatform | None":
        try:
            return cls(str(source).upper())
        except ValueError:
            return None

    @property
    def is_email(self) -> bool:
        return self is SurfacePlatform.RESEND


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
    # Did the receiving mail service vouch for ``sender_email``? Email only:
    # None everywhere else, where a signed platform payload asserts the sender
    # and there is nothing to authenticate. See ``email_authentication``.
    sender_authentication: str | None = None
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


class SurfaceLifecycleKind(StrEnum):
    """What happened to the app itself, as opposed to what someone said to it."""

    # The bot was added to a channel. Carries the person who added it — the one
    # worth asking which agent should answer there.
    JOINED_CHANNEL = "JOINED_CHANNEL"
    # Someone opened the app's own surface (Slack's App Home / agent DM tab).
    HOME_OPENED = "HOME_OPENED"


class ParsedSurfaceLifecycleEvent(BaseModel):
    """The third kind of inbound event: neither a message nor an interaction.

    A message becomes a conversation; an interaction resumes a paused run. These
    do neither — nothing here reaches an agent. They are the platform telling us
    the app's own situation changed, and the only correct response is to
    configure something or to offer to.

    Kept a separate contract on purpose: folding them into the message parser
    would mean every consumer of ``ParsedInboundSurfaceEvent`` grows a branch for
    events that carry no message and start no run.
    """

    platform: SurfacePlatform
    kind: SurfaceLifecycleKind
    tenant_id: str | None = None
    external_channel_id: str | None = None
    # Who caused it: the inviter for JOINED_CHANNEL, the viewer for HOME_OPENED.
    actor_external_user_id: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


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
    schedule_id: UUID | None = None  # Gmail/Outlook: linked email schedule
    surface_identity_email: str | None = None  # Gmail/Outlook: for self-email filtering
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

        entity = cls(
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
        # `SurfaceRepository.create` already drains this via `_collect_events`;
        # the entity simply never recorded anything.
        from app.core.authorization.current import get_current_context

        actor = get_current_context()
        entity.add_event(
            SurfaceConnectedEvent(
                surface_id=entity.id,
                pod_id=pod_id,
                platform=resolved.value,
                agent_id=agent_id,
                connected_by_user_id=getattr(actor, "user_id", None),
            )
        )
        return entity

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
            raise AgentSurfaceValidationError("EMAIL mode is only supported for email")
        if (
            surface_type in {SurfacePlatform.SLACK, SurfacePlatform.TEAMS}
            and account_id is None
        ):
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
        # Every surface receives over a native webhook. Polling existed only for
        # the Composio-backed mailboxes, and they are gone.
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
        next_mode = (
            self._resolve_mode(self.surface_type, mode)
            if mode is not None
            else self.mode
        )
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
            AgentSurfaceStatus.ACTIVE if is_active else AgentSurfaceStatus.INACTIVE
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
        if (
            self.surface_type is not SurfacePlatform.TELEGRAM
            and not self.matches_channel(event.external_channel_id)
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
    # When this person last wrote to us. Distinct from ``updated_at``, which an
    # outbound notification also bumps — keying the DM reset off ``updated_at``
    # means a proactive message silently suppresses the reset and leaks
    # yesterday's context into today. Also the ranking key when choosing which of
    # someone's channels to reach them on: freshest inbound is the best available
    # guess at where they are actually looking.
    last_inbound_at: datetime | None = None

    @property
    def inbound_activity_at(self) -> datetime:
        """Last inbound time, falling back to ``updated_at`` for legacy rows.

        Rows written before ``last_inbound_at`` existed have NULL there, and for
        those ``updated_at`` *was* the last inbound time, because until then only
        inbound events wrote this row. The fallback is correct by definition, and
        it has to survive in code as well as in the backfill: a row the backfill
        touched can still be NULL if it was created by an older worker mid-deploy.
        """
        return self.last_inbound_at or self.updated_at

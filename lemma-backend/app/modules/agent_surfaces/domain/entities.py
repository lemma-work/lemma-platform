from __future__ import annotations

from enum import StrEnum
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

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


class SurfaceSendPolicy(BaseModel):
    """Controls proactive sending (``surface.send``) for a surface."""

    # Expose the current-user ``surface_send_message`` tool to the agent.
    allow_send: bool = False


class SurfaceConfig(BaseModel):
    """User-editable surface behavior. Exactly what the API accepts and returns.

    Derived/identity data (workspace ids, secrets, schedule links) lives in
    dedicated entity fields, never in here.
    """

    dm_conversation_reset_after_hours: int = 24
    identity: SurfaceIdentityPolicy = Field(default_factory=SurfaceIdentityPolicy)
    channels: list[SurfaceChannelRoute] = Field(default_factory=list)
    send_policy: SurfaceSendPolicy = Field(default_factory=SurfaceSendPolicy)


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


class ParsedSurfaceInteraction(BaseModel):
    """A native-form submission (Slack block_actions / Teams Action.Submit).

    ``callback_id`` encodes ``conversation_id|tool_call_id`` (see
    ``display_resource_renderer.parse_callback_id``) so the submission can be
    routed back to the originating conversation; ``values`` holds the collected
    field name → value map; ``dedup_id`` uniquely identifies this submission for
    replay protection.
    """

    platform: SurfacePlatform
    tenant_id: str | None = None
    external_channel_id: str | None = None
    external_thread_id: str | None = None
    external_user_id: str | None = None
    callback_id: str
    values: dict[str, Any] = Field(default_factory=dict)
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
            if (
                resolved is SurfacePlatform.TEAMS
                and account_id is None
            )
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
            return SurfaceMode(mode.value if hasattr(mode, "value") else str(mode))
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
                event_mode.value if hasattr(event_mode, "value") else str(event_mode)
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

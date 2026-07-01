from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.domain.models import SurfaceChannelInfo
from app.modules.agent_surfaces.domain.models import SurfaceContextMessage
from app.modules.agent_surfaces.domain.models import SurfaceQuestionRenderPlan


class SurfaceAccountInfo(BaseModel):
    id: UUID
    user_id: UUID
    organization_id: UUID | None = None
    auth_config_id: UUID | None = None
    email: str | None = None
    connector_id: str
    credentials: dict[str, Any] = {}


class SurfaceAccountPort(Protocol):
    async def get_account(self, account_id: UUID) -> SurfaceAccountInfo | None: ...


class SurfaceAuthConfigInfo(BaseModel):
    id: UUID
    provider: str
    connector_id: str
    # "SYSTEM_DEFAULT" (Lemma's own OAuth app) or "ORG_CUSTOM" (org brought its
    # own app). Drives whether the org must wire up its own provider webhook.
    config_source: str | None = None


class SurfaceAuthConfigPort(Protocol):
    async def get_auth_config(
        self, auth_config_id: UUID
    ) -> SurfaceAuthConfigInfo | None: ...


class SurfaceInstallationRepositoryPort(Protocol):
    async def get(self, id: UUID) -> AgentSurfaceEntity | None: ...

    async def merge_conversation_metadata(
        self, conversation_id: UUID, updates: dict
    ) -> None: ...

    async def get_by_pod_and_name(
        self,
        *,
        pod_id: UUID,
        name: str,
    ) -> AgentSurfaceEntity | None: ...

    async def get_active_by_address(
        self,
        *,
        platform: str,
        address: str,
    ) -> AgentSurfaceEntity | None: ...

    async def list_by_pod(
        self,
        pod_id: UUID,
        *,
        platform: str | None = None,
        agent_id: UUID | None = None,
        match_agent: bool = False,
        cursor: UUID | None = None,
        limit: int = 100,
    ) -> tuple[list[AgentSurfaceEntity], UUID | None]: ...

    async def list_active_by_type(
        self, surface_type: str
    ) -> list[AgentSurfaceEntity]: ...

    async def list_active_native_receiver_surfaces(
        self,
        platforms: set[SurfacePlatform],
    ) -> list[AgentSurfaceEntity]: ...

    async def get_by_email_schedule_id(
        self, schedule_id: UUID
    ) -> AgentSurfaceEntity | None: ...

    async def get_by_platform_and_account_id(
        self,
        *,
        platform: str,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def get_system_credential_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        platform: str,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def get_account_conflict_in_org(
        self,
        *,
        pod_id: UUID,
        account_id: UUID,
        exclude_surface_id: UUID | None = None,
    ) -> AgentSurfaceEntity | None: ...

    async def create(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity: ...

    async def update(self, entity: AgentSurfaceEntity) -> AgentSurfaceEntity: ...

    async def delete(self, id: UUID) -> None: ...


class SurfaceAccountBindingPort(Protocol):
    """Validates the connected account for a platform and derives the
    non-secret routing identity fields."""

    async def resolve_binding(
        self,
        platform: SurfacePlatform,
        account_id: UUID | None = None,
    ) -> tuple[str | None, str | None, str | None]: ...

    # Returns (external_tenant_id, external_workspace_id, surface_identity_id).


class SurfacePlatformAdapterPort(Protocol):
    platform: str

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None: ...

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent: ...

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None: ...

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def send_display_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def send_questions(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: "SurfaceQuestionRenderPlan",
        metadata: dict[str, Any] | None = None,
    ) -> bool: ...

    async def send_voice_note(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        audio_bytes: bytes,
        mime: str,
        caption: str | None = None,
    ) -> bool: ...

    # Deliver audio as a native voice note (Telegram sendVoice, etc.). True →
    # delivered natively; False → caller falls back to a normal file attachment.

    # Render ask_user questions as native tappable choices (Slack input blocks /
    # Teams Adaptive Card). True → rendered natively; False → caller falls back to
    # a formatted text message.

    async def fetch_thread_context(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list["SurfaceContextMessage"]: ...

    # Fetch the last few messages of the inbound thread/channel for background
    # context on a group mention (each user has a separate conversation, so this
    # gives continuity). Best-effort, fetched fresh per run. Default: none.

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> "ParsedSurfaceInteraction | None": ...

    # Parse an interaction submission (Slack block_actions, Teams Action.Submit)
    # into a routable interaction, or None when the payload is not an interaction.

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    # Show live progress text on platforms that support an editable message
    # (Telegram, Teams). Returns an opaque handle (e.g. {"message_id": ...}) to
    # pass back on the next call so the same message is edited. None → platform
    # has no editable progress; the caller keeps using typing indicators.

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None: ...

    # Clean up the streaming progress message at run end (e.g. delete it before
    # the final answer is delivered).

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None: ...

    # (content, file_name, mime_type) for a user-provided inbound attachment, or
    # None when it cannot be downloaded. Used by inbound auto-ingest; not an
    # agent tool.

    async def send_file_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool: ...

    # True when the file was delivered natively; False → caller should fall back
    # to sending an app/public URL link instead.

    async def list_channels(
        self, *, credentials: dict[str, Any]
    ) -> list[SurfaceChannelInfo]: ...

    # Channels/groups the bot can be configured in (Slack/Teams). Empty for
    # platforms without an enumerable channel concept.

    def unresolved_sender_reply(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None: ...

    # (message, reply_metadata) for unresolved senders; None → default signup prompt.

    def linked_sender_confirmation(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None: ...

    # Non-None → send this reply instead of starting a chat (identity-link events).


class SurfaceEventDedupStorePort(Protocol):
    async def claim_message(
        self,
        *,
        surface_installation_id: UUID,
        platform: str,
        external_channel_id: str | None,
        external_thread_id: str | None,
        external_message_id: str | None,
    ) -> bool: ...


class SurfacePodMembershipPort(Protocol):
    """Port for resolving pod membership for surface routing checks."""

    async def get_user_pod_ids(self, user_id: UUID) -> list[UUID]: ...

    async def get_user_email(self, user_id: UUID) -> str | None: ...

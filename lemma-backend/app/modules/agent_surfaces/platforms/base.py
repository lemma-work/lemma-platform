"""Shared base for surface platform adapters.

Implements the optional parts of ``SurfacePlatformAdapterPort`` so concrete
adapters only override what their platform actually needs.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceChannelInfo,
    SurfaceContextMessage,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)


class BaseSurfaceAdapter:
    platform: str

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent | None:
        del credentials
        return event

    def unresolved_sender_reply(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None:
        """Platform-specific reply for senders that could not be resolved to an
        internal user. Return ``(message, reply_metadata)`` or None to fall back
        to the default signup prompt."""
        del event
        return None

    def linked_sender_confirmation(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, dict[str, Any]] | None:
        """Confirmation reply to send instead of starting a chat when the event
        only completed an identity-linking step (e.g. Telegram contact share)."""
        del event
        return None

    async def send_display_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.send_message(
            credentials=credentials,
            event=event,
            message=render_plan.to_plain_text(),
            metadata=metadata,
        )

    async def send_questions(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render ask_user questions as native tappable choices. Default: not
        supported → False so the caller falls back to a formatted text message."""
        del credentials, event, question_plan, metadata
        return False

    async def send_approval(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as native Approve/Deny buttons.
        Default: not supported → False so the caller falls back to a text prompt."""
        del credentials, event, approval_plan, metadata
        return False

    async def send_voice_note(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        audio_bytes: bytes,
        mime: str,
        caption: str | None = None,
    ) -> bool:
        """Deliver audio as a native voice note. Default: not supported → False so
        the caller falls back to a normal file attachment (an inline audio player
        on most platforms)."""
        del credentials, event, file_name, audio_bytes, mime, caption
        return False

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """Parse a form/interaction submission. Default: platform has no
        interactions → None."""
        del payload, headers
        return None

    async def fetch_thread_context(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """Fetch recent thread/channel messages for background context. Default:
        platform can't fetch history (or has none) → empty list."""
        del credentials, event, limit
        return []

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Show live progress text. Default: platform has no editable progress
        message → return None so the caller keeps using typing indicators."""
        del credentials, event, progress_text, progress_handle
        return None

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Clean up the streaming progress message. Default: no-op."""
        del credentials, event, progress_handle

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a user-provided inbound attachment for auto-ingest.

        Default: platform has no downloadable attachments. Override per platform.
        """
        del credentials, event, attachment
        return None

    async def send_file_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Deliver a file's bytes natively on the platform.

        Default: platform has no native file send → return False so the caller
        falls back to sending an app/public URL link.
        """
        del credentials, event, file_name, file_bytes, mime_type, caption
        return False

    async def list_channels(
        self, *, credentials: dict[str, Any]
    ) -> list[SurfaceChannelInfo]:
        """List channels/groups the bot can be configured in.

        Default: platform has no enumerable channels (DMs/groups the bot is
        added to but cannot list, e.g. Telegram/WhatsApp/email).
        """
        del credentials
        return []

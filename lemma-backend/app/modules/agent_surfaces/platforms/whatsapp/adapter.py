"""WhatsApp surface adapter."""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
    split_whatsapp_deliveries,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)


class WhatsAppSurfaceAdapter(BaseSurfaceAdapter):
    platform = "WHATSAPP"

    def __init__(self) -> None:
        self._parser = WhatsAppMessageParser()

    def split_inbound_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return split_whatsapp_deliveries(payload)

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self._parser.parse(payload, headers)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return await WhatsAppPlatformService(credentials).fetch_sender_profile(event)

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await WhatsAppPlatformService(credentials).send_message(
            event, message, metadata
        )

    async def _render_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await WhatsAppPlatformService(credentials)._render_resource(
            event,
            render_plan,
            metadata,
        )

    async def _render_choices(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials)._render_choices(
            event, question_plan, metadata
        )

    async def _render_decision(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials)._render_decision(
            event, approval_plan, metadata
        )

    async def acknowledge_interaction(
        self,
        *,
        credentials: dict[str, Any],
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        await WhatsAppPlatformService(credentials).acknowledge_interaction(
            interaction,
            text=text,
            show_alert=show_alert,
            clear_actions=clear_actions,
        )

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self._parser.parse_interaction(payload, headers)

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Post a progress update as a new message.

        Returns ``None`` deliberately: a handle is a reference to a live message
        that later edits and the end-of-run cleanup act on, and WhatsApp has no
        such message. Each update stands on its own and there is nothing to
        clear afterwards.
        """
        del progress_handle, metadata
        await WhatsAppPlatformService(credentials).stream_progress(event, progress_text)
        return None

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await WhatsAppPlatformService(credentials).add_processing_indicator(
            event, metadata
        )

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        return await WhatsAppPlatformService(credentials).download_attachment_bytes(
            event, attachment
        )

    async def _render_file(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        return await WhatsAppPlatformService(credentials).send_file_bytes(
            event,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            caption=caption,
        )

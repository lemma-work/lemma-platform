from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceChannelInfo,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService


class SlackSurfaceAdapter(BaseSurfaceAdapter):
    platform = "SLACK"

    def __init__(self) -> None:
        self.parser = SlackMessageParser()

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self.parser.parse(payload, headers)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return await self._service(credentials).fetch_sender_profile(event=event)

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._service(credentials).send_message(
            event=event,
            message=message,
            metadata=metadata,
        )

    async def send_display_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._service(credentials).send_display_resource(
            event=event,
            render_plan=render_plan,
            metadata=metadata,
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._service(credentials).add_processing_indicator(
            event=event,
            metadata=metadata,
        )

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await self._service(credentials).stream_progress(
            event, progress_text, progress_handle
        )

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        await self._service(credentials).end_progress(event, progress_handle)

    async def send_questions(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._service(credentials).send_questions(
            event=event, question_plan=question_plan, metadata=metadata
        )

    async def send_approval(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._service(credentials).send_approval(
            event=event, approval_plan=approval_plan, metadata=metadata
        )

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self.parser.parse_interaction(payload, headers)

    async def fetch_thread_context(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ):
        return await self._service(credentials).fetch_recent_context(
            event=event, limit=limit
        )

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        return await self._service(credentials).download_attachment_bytes(
            event, attachment
        )

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
        return await self._service(credentials).send_file_bytes(
            event,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            caption=caption,
        )

    async def list_channels(
        self, *, credentials: dict[str, Any]
    ) -> list[SurfaceChannelInfo]:
        return await self._service(credentials).list_channels()

    def _service(self, credentials: dict[str, Any]) -> SlackPlatformService:
        return SlackPlatformService(credentials=credentials, parser=self.parser)

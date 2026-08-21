from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ParsedSurfaceLifecycleEvent,
)
from app.modules.agent_surfaces.domain.models import (
    StreamAppendResult,
    SurfaceApprovalRenderPlan,
    SurfaceChannelInfo,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.slack.home import SlackHomeSurface
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.slack.streaming import SlackStreamSurface


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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await self._stream(credentials).stream_progress(
            event, progress_text, progress_handle, metadata
        )

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        await self._stream(credentials).end_progress(event, progress_handle)

    async def append_stream_text(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> StreamAppendResult:
        return await self._stream(credentials).append_stream_text(
            event, progress_handle, text, metadata
        )

    async def finish_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._stream(credentials).finish_progress(
            event, progress_handle, message, metadata
        )

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

    async def parse_inbound_lifecycle(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceLifecycleEvent | None:
        return self.parser.parse_lifecycle(payload, headers)

    async def send_channel_setup_prompt(
        self,
        *,
        credentials: dict[str, Any],
        channel_id: str,
        user_id: str,
        channel_name: str | None = None,
        confirmed_agent: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        configuration_error: str | None = None,
    ) -> bool:
        return await self._home(credentials).send_channel_setup_prompt(
            channel_id=channel_id,
            user_id=user_id,
            channel_name=channel_name,
            confirmed_agent=confirmed_agent,
            surface_choices=surface_choices,
            configuration_error=configuration_error,
        )

    async def parse_channel_setup(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return self.parser.parse_channel_setup(payload, headers)

    async def open_channel_setup_modal(
        self,
        *,
        credentials: dict[str, Any],
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_names: list[str],
        surface_id: str | None = None,
    ) -> bool:
        return await self._home(credentials).open_channel_setup_modal(
            trigger_id=trigger_id,
            channel_id=channel_id,
            channel_label=channel_label,
            agent_names=agent_names,
            surface_id=surface_id,
        )

    async def send_starter_prompt(
        self, *, credentials: dict[str, Any], user_id: str, prompt: str
    ) -> bool:
        return await self._home(credentials).send_starter_prompt(
            user_id=user_id, prompt=prompt
        )

    async def open_dm_agent_modal(
        self,
        *,
        credentials: dict[str, Any],
        trigger_id: str,
        agent_names: list,
        current: str | None,
        surface_id: str | None = None,
    ) -> bool:
        return await self._home(credentials).open_dm_agent_modal(
            trigger_id=trigger_id,
            agent_names=list(agent_names),
            current=current,
            surface_id=surface_id,
        )

    async def publish_home_view(
        self,
        *,
        credentials: dict[str, Any],
        user_id: str,
        pod_name: str | None,
        dm_agent_name: str | None,
        channel_routes: list,
        agents: list | None = None,
        apps: list | None = None,
        workspace_url: str | None = None,
        logo_url: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        access_message: str | None = None,
    ) -> bool:
        return await self._home(credentials).publish_home_view(
            user_id=user_id,
            pod_name=pod_name,
            dm_agent_name=dm_agent_name,
            channel_routes=channel_routes,
            agents=agents,
            apps=apps,
            workspace_url=workspace_url,
            logo_url=logo_url,
            surface_choices=surface_choices,
            access_message=access_message,
        )

    async def channel_name(
        self, *, credentials: dict[str, Any], channel_id: str
    ) -> str | None:
        return await self._home(credentials).channel_name(channel_id)

    async def set_thread_title(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        return await self._home(credentials).set_thread_title(event=event, title=title)

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

    def _home(self, credentials: dict[str, Any]) -> SlackHomeSurface:
        return SlackHomeSurface(credentials=credentials)

    def _stream(self, credentials: dict[str, Any]) -> SlackStreamSurface:
        return SlackStreamSurface(credentials=credentials)

from __future__ import annotations

from app.modules.agent_surfaces.domain.routing_facts import InboundRoutingFacts

from app.modules.agent_surfaces.domain.channel_setup import ChannelSetupRequest

from app.modules.agent_surfaces.domain.models import ProgressHandle

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

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
from app.modules.agent_surfaces.domain.place_privacy import PlaceFacts
from app.modules.agent_surfaces.domain.groups import ParsedGroupUpdate
from app.modules.agent_surfaces.domain.line_changes import ParsedLineChange
from app.modules.agent_surfaces.platforms.slack.line_changes import slack_line_changes
from app.modules.agent_surfaces.platforms.slack.group_updates import (
    slack_group_updates,
)
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
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

    def mention_is_provisional(self, event: ParsedInboundSurfaceEvent) -> bool:
        """A plain ``message`` that @-mentions anyone reads as addressed to us,
        because that is all its payload can tell. ``app_mention`` is delivered
        only to the app that was mentioned, so it is settled already."""
        return (
            not event.is_dm
            and event.mentioned_agent
            and InboundRoutingFacts.of(event).event_type != "app_mention"
        )

    async def settle_mention(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        bot_user_id: str | None,
    ) -> ParsedInboundSurfaceEvent:
        """Addressed only if this bot's own user id is among those mentioned.

        Fails closed without a bot id to compare, which a surface created
        through the bundle or CLI path has not recorded: reading that as
        "addressed" had the agent answer every channel message in which two
        colleagues mentioned each other.
        """
        del credentials
        if not self.mention_is_provisional(event):
            return event
        mentioned = InboundRoutingFacts.of(event).mentioned_user_ids
        return event.model_copy(
            update={"mentioned_agent": bool(bot_user_id) and bot_user_id in mentioned}
        )

    async def fetch_sender_profile(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return await self._service(credentials).fetch_sender_profile(event=event)

    async def send_message(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        return await self._service(credentials).send_message(
            event=event,
            message=message,
            metadata=metadata,
        )

    async def _render_resource(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._service(credentials)._render_resource(
            event=event,
            render_plan=render_plan,
            metadata=metadata,
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._service(credentials).add_processing_indicator(
            event=event,
            metadata=metadata,
        )

    async def end_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None = None,
    ) -> None:
        await self._stream(credentials).end_progress(event, progress_handle)

    async def append_stream_text(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> StreamAppendResult:
        return await self._stream(credentials).append_stream_text(
            event, progress_handle, text, metadata
        )

    async def finish_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._stream(credentials).finish_progress(
            event, progress_handle, message, metadata
        )

    async def _render_choices(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._service(credentials)._render_choices(
            event=event, question_plan=question_plan, metadata=metadata
        )

    async def _render_decision(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        return await self._service(credentials)._render_decision(
            event=event, approval_plan=approval_plan, metadata=metadata
        )

    async def retire_decision_card(
        self,
        *,
        credentials: SurfaceCredentials,
        handle: DecisionCardHandle,
        reason: CardRetirement = CardRetirement.SUPERSEDED,
    ) -> None:
        await self._service(credentials).retire_decision_card(handle, reason)

    def parse_group_updates(
        self, payload: dict[str, object]
    ) -> list[ParsedGroupUpdate]:
        return slack_group_updates(payload)

    def parse_line_changes(self, payload: dict[str, object]) -> list[ParsedLineChange]:
        return slack_line_changes(payload, self.parser)

    async def parse_inbound_lifecycle(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceLifecycleEvent | None:
        return self.parser.parse_lifecycle(payload, headers)

    async def send_channel_setup_prompt(
        self,
        *,
        credentials: SurfaceCredentials,
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
    ) -> ChannelSetupRequest | None:
        return self.parser.parse_channel_setup(payload, headers)

    async def open_channel_setup_modal(
        self,
        *,
        credentials: SurfaceCredentials,
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_name: str,
        surface_id: str | None = None,
    ) -> bool:
        return await self._home(credentials).open_channel_setup_modal(
            trigger_id=trigger_id,
            channel_id=channel_id,
            channel_label=channel_label,
            agent_name=agent_name,
            surface_id=surface_id,
        )

    async def send_starter_prompt(
        self, *, credentials: SurfaceCredentials, user_id: str, prompt: str
    ) -> bool:
        return await self._home(credentials).send_starter_prompt(
            user_id=user_id, prompt=prompt
        )

    async def publish_home_view(
        self,
        *,
        credentials: SurfaceCredentials,
        user_id: str,
        pod_name: str | None,
        agent_name: str,
        channel_ids: list[str],
        agents: list[tuple[str, str | None]] | None = None,
        apps: list[tuple[str, str]] | None = None,
        workspace_url: str | None = None,
        logo_url: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        access_message: str | None = None,
    ) -> bool:
        return await self._home(credentials).publish_home_view(
            user_id=user_id,
            pod_name=pod_name,
            agent_name=agent_name,
            channel_ids=channel_ids,
            agents=agents,
            apps=apps,
            workspace_url=workspace_url,
            logo_url=logo_url,
            surface_choices=surface_choices,
            access_message=access_message,
        )

    async def channel_name(
        self, *, credentials: SurfaceCredentials, channel_id: str
    ) -> str | None:
        return await self._home(credentials).channel_name(channel_id)

    async def read_place_privacy(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> PlaceFacts | None:
        channel_id = event.external_channel_id
        if not channel_id:
            return None
        return await self._home(credentials).place_facts(channel_id)

    async def set_thread_title(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        return await self._home(credentials).set_thread_title(event=event, title=title)

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self.parser.parse_interaction(payload, headers)

    async def acknowledge_interaction(
        self,
        *,
        credentials: SurfaceCredentials,
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        await self._service(credentials).acknowledge_interaction(
            interaction,
            text=text,
            show_alert=show_alert,
            clear_actions=clear_actions,
        )

    async def fetch_thread_context(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ):
        return await self._service(credentials).fetch_recent_context(
            event=event, limit=limit
        )

    async def download_attachment(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        return await self._service(credentials).download_attachment_bytes(
            event, attachment
        )

    async def _render_file(
        self,
        *,
        credentials: SurfaceCredentials,
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
        self, *, credentials: SurfaceCredentials
    ) -> list[SurfaceChannelInfo]:
        return await self._service(credentials).list_channels()

    def _service(self, credentials: SurfaceCredentials) -> SlackPlatformService:
        return SlackPlatformService(credentials=credentials, parser=self.parser)

    def _home(self, credentials: SurfaceCredentials) -> SlackHomeSurface:
        return SlackHomeSurface(credentials=credentials)

    def _stream(self, credentials: SurfaceCredentials) -> SlackStreamSurface:
        return SlackStreamSurface(credentials=credentials)

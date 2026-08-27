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
    ColdEmailSendResult,
    StreamAppendResult,
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

    async def send_cold_email(
        self,
        *,
        credentials: dict[str, Any],
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> "ColdEmailSendResult | None":
        """Start an email thread with somebody who has never written to us.

        The one thing email can do that chat cannot: address a mailbox with no
        prior message to reply to. Default: not supported → None, which is a
        clean "this platform can't", not an error — Outlook and Composio-backed
        Gmail both reply through endpoints keyed by a provider message id they
        would not have.

        ``thread_seed_id`` is the Message-ID the caller will key the reply on;
        an implementation must ensure the recipient's reply carries it (in
        ``References``) or return a thread id its own parser will derive
        instead.
        """
        del credentials, recipient_email, subject, message, thread_seed_id, metadata
        return None

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """Parse a form/interaction submission. Default: platform has no
        interactions → None."""
        del payload, headers
        return None

    async def acknowledge_interaction(
        self,
        *,
        credentials: dict[str, Any],
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        del credentials, interaction, text, show_alert, clear_actions

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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Show live progress text. Default: platform has no editable progress
        message → return None so the caller keeps using typing indicators."""
        del credentials, event, progress_text, progress_handle, metadata
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

    async def parse_inbound_lifecycle(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ):
        """Parse an event about the app itself. Default: not a lifecycle event."""
        del payload, headers
        return None

    # The in-chat set-up flow. Only Slack drives configuration from inside the
    # chat app today, but `SurfaceConfigurationMixin` calls all of these on
    # whichever adapter the inbound webhook resolved to — so they are part of
    # the adapter contract, not Slack's private surface. Declared here with
    # inert defaults: a platform that cannot configure itself in-chat answers
    # "not mine" to the parse and "didn't do it" to the rest.
    async def parse_channel_setup(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Parse a payload belonging to the in-chat set-up flow. Default: the
        platform drives set-up from the web UI only → None, so the caller keeps
        treating the payload as an ordinary message."""
        del payload, headers
        return None

    async def channel_name(
        self, *, credentials: dict[str, Any], channel_id: str
    ) -> str | None:
        """Human-readable name for a channel. Default: unknown."""
        del credentials, channel_id
        return None

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
        """Ask, in-chat, which agent answers in a channel. Default: unsupported."""
        del credentials, trigger_id, channel_id, channel_label, agent_names
        del surface_id
        return False

    async def open_dm_agent_modal(
        self,
        *,
        credentials: dict[str, Any],
        trigger_id: str,
        agent_names: list,
        current: str | None,
        surface_id: str | None = None,
    ) -> bool:
        """Ask, in-chat, which agent answers a person's DMs. Default: unsupported."""
        del credentials, trigger_id, agent_names, current, surface_id
        return False

    async def send_starter_prompt(
        self, *, credentials: dict[str, Any], user_id: str, prompt: str
    ) -> bool:
        """Offer an opening prompt to a new user. Default: unsupported."""
        del credentials, user_id, prompt
        return False

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
        offers_dm_agent_choice: bool = True,
    ) -> bool:
        """Render the app's home tab. Default: the platform has no home tab."""
        del credentials, user_id, pod_name, dm_agent_name, channel_routes
        del agents, apps, workspace_url, logo_url, surface_choices, access_message
        del offers_dm_agent_choice
        return False

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
        """Offer to configure a freshly joined channel. Default: unsupported."""
        del (
            credentials,
            channel_id,
            user_id,
            channel_name,
            confirmed_agent,
            surface_choices,
            configuration_error,
        )
        return False

    async def set_thread_title(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        """Name the conversation thread on the platform. Default: unsupported."""
        del credentials, event, title
        return False

    async def append_stream_text(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> StreamAppendResult:
        """Append model text to a live stream. Default: platform cannot stream."""
        del credentials, event, text, metadata
        return StreamAppendResult(handle=progress_handle, appended=False)

    async def finish_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Close a live progress stream *with* the final answer, as one message.

        Only platforms with a real streaming API can do this. Returning False
        means "I did not deliver the answer" — the caller then clears progress
        and sends the answer as its own message, which is the path every other
        platform takes.
        """
        del credentials, event, progress_handle, message, metadata
        return False

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
        falls back to sending a Lemma app deep link (not a public download
        URL — it only opens for a recipient with pod access).
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

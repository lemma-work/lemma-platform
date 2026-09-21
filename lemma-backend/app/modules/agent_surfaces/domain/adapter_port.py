"""What every chat platform has to be able to do.

One contract, implemented by every adapter under `platforms/` and by nothing
else. It lives apart from `ports.py`, which holds the module's *infrastructure*
seams -- repositories, a dedup store, a user directory, a membership lookup,
each with one implementation in `infrastructure/`. Those answer "where does this
row come from". This answers "what can Slack do that email cannot", which is the
module's central question and the thing `platform_capabilities` describes in
prose.

The split is not a page count. It is that four separate reviews found this
protocol missing an operation every adapter already implemented -- most recently
the four in-chat set-up calls below -- and each time it was invisible because
the caller reached the adapter through something the type checker could not see
through. Keeping the platform contract in a file of its own is what makes "does
the port say this exists?" a question with somewhere to look.

`BaseSurfaceAdapter` supplies a default for everything optional here, so an
adapter declares only what its platform can actually do.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ParsedSurfaceLifecycleEvent,
)
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, SurfaceEnvelope
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
    StreamAppendResult,
    SurfaceChannelInfo,
    SurfaceContextMessage,
    SurfaceSenderProfile,
)


class SurfacePlatformAdapterPort(Protocol):
    platform: str

    def split_inbound_payloads(
        self, payload: dict[str, Any]
    ) -> list[dict[str, Any]]: ...

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

    # The text primitive `deliver` degrades onto, and the only way to say
    # something before a conversation exists (the signup and setup replies).

    async def deliver(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryReceipt:
        """The one outbound seam for conversation content.

        Every kind of content is a field on the envelope, and the receipt says
        how each part landed -- natively, degraded to text or a link, or
        reaching nobody.

        The ``_render_*`` hooks it composes are deliberately not declared on
        this port. They are a platform's private half of this call, and naming
        them here made the seam read as six verbs a caller could choose between
        -- which is how content came to be rendered past ``deliver`` in the
        first place.
        """
        ...

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

    async def acknowledge_interaction(
        self,
        *,
        credentials: dict[str, Any],
        interaction: "ParsedSurfaceInteraction",
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        raise NotImplementedError

    # Parse an interaction submission (Slack block_actions, Teams Action.Submit)
    # into a routable interaction, or None when the payload is not an interaction.

    async def set_thread_title(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool: ...

    # Name the thread on the platform, where it has a name to set. False on
    # every platform that does not, which is the default on `BaseSurfaceAdapter`
    # -- best-effort by construction, and called once, when a conversation is
    # brand new.

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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None: ...

    # Show live progress text on platforms that support an editable message
    # (Telegram, Teams). Returns an opaque handle (e.g. {"message_id": ...}) to
    # pass back on the next call so the same message is edited. None → platform
    # has no editable progress; the caller keeps using typing indicators.
    #
    # `metadata` carries the agent's display name and icon: the answer that
    # closes this same message is authored by the agent, so the stream must be
    # too or the thread reads as two speakers. It was on every adapter and
    # absent here, which is why `SurfaceProgress` calling it was a type error.

    async def append_stream_text(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> StreamAppendResult: ...

    # Append model text to a live stream, token by token. The default on
    # `BaseSurfaceAdapter` reports `appended=False`, which is every platform
    # except Slack.

    async def finish_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool: ...

    # Close a live stream *with* the final answer, so steps and answer are one
    # message. False means "I did not deliver it" and the caller falls back to
    # clearing progress and sending the answer separately.

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None: ...

    # Clean up the streaming progress message at run end (e.g. delete it before
    # the final answer is delivered).

    # The in-chat set-up flow. Only Slack drives configuration from inside the
    # chat app today, and every other adapter takes `BaseSurfaceAdapter`'s
    # default -- but `AppEventHandler` calls all four on whatever the registry
    # hands it, so the port has to say they exist. It did not, which is the
    # fourth time a port here has omitted an operation every adapter implements;
    # each time it was invisible until a caller the type checker could see
    # through reached for it.

    async def publish_home_view(
        self,
        *,
        credentials: dict[str, Any],
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
    ) -> bool: ...

    # The app's home tab. False where the platform has none. `agent_name` is
    # required and has no default on any implementation -- which one caller had
    # been omitting, on the path that tells somebody they have no access to a
    # connected pod, so that message raised `TypeError` instead of rendering.

    async def parse_channel_setup(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None: ...

    # A payload belonging to the in-chat set-up flow, or None when this platform
    # drives set-up from the web UI only -- in which case the caller goes on
    # treating the payload as an ordinary message.

    async def parse_inbound_lifecycle(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceLifecycleEvent | None: ...

    # An event about the app itself -- installed, added to a channel, home
    # opened. None when the payload is not one.

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
    ) -> bool: ...

    # Offer to configure a freshly joined channel. False where the platform has
    # nowhere to put the offer.

    async def channel_name(
        self, *, credentials: dict[str, Any], channel_id: str
    ) -> str | None: ...

    # What a person calls this channel, where the platform can say.

    async def send_cold_email(
        self,
        *,
        credentials: dict[str, Any],
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ColdEmailSendResult | None: ...

    # Start a thread with somebody who has never written to us. None on every
    # platform that cannot address a recipient it has no prior message from,
    # which is all of them except mail.

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

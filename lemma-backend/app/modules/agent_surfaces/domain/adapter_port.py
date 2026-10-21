"""What every chat platform has to be able to do, split by use case.

One adapter per platform implements these, under `platforms/`, and nothing
else does. It lives apart from `ports.py`, which holds the module's
*infrastructure* seams -- repositories, a dedup store, a user directory -- each
with one implementation in `infrastructure/`. Those answer "where does this row
come from". This answers "what can Slack do that email cannot".

It was one protocol of twenty-six methods, and four reviews running found it
missing an operation every adapter implemented, each time invisible because the
caller reached the adapter through something the type checker could not see
through. Split by who calls what:

- `InboundParser` reads a platform's payload. Pure: no credentials, no I/O.
- `InboundReader` reads what a payload did not carry -- the sender's profile,
  the thread so far, an attachment's bytes -- with the surface's credentials.
- `OutboundDelivery` is everything that reaches the person: the reply, the
  progress line, the answer to a tap.
- `InChatSetup` is optional. Only a platform that is configured from inside the
  chat app (Slack's home tab and channel modal) implements it, and callers ask
  `in_chat_setup(adapter)` rather than calling inert defaults on every platform.

`SurfacePlatformAdapterPort` is the three a platform must have, which is what
the registry holds. `BaseSurfaceAdapter` supplies defaults for the optional
parts of those three, so an adapter declares only what its platform can do.

Credentials are the platform's model (`domain.credentials`). A progress line is
a `ProgressHandle`. Outbound `metadata` is still a mapping: the agent's display
name and icon and a platform's own extras ride in it.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeIs

from app.modules.agent_surfaces.domain.channel_setup import ChannelSetupRequest
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ParsedSurfaceLifecycleEvent,
)
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, SurfaceEnvelope
from app.modules.agent_surfaces.domain.groups import ParsedGroupUpdate
from app.modules.agent_surfaces.domain.line_changes import ParsedLineChange
from app.modules.agent_surfaces.domain.place_privacy import PlaceFacts
from app.modules.agent_surfaces.domain.surface_config import ReengageTemplate
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
    ProgressHandle,
    StreamAppendResult,
    SurfaceChannelInfo,
    SurfaceContextMessage,
    SurfaceSenderProfile,
)

# Any: a webhook body and an outbound metadata mapping are provider JSON with no
# shape until the platform's own parser or sender reads them.
Payload = dict[str, Any]
OutboundMetadata = dict[str, Any]

#: Outbound metadata naming the send, for a platform that dedupes on a key
#: (Resend): every try of one delivery is then the same message.
IDEMPOTENCY_KEY = "idempotency_key"


class InboundParser(Protocol):
    """Reading a platform's payload into the module's events. No I/O."""

    platform: str

    def split_inbound_payloads(self, payload: Payload) -> list[Payload]:
        """One delivery, as the one-or-more messages it carries."""
        ...

    async def parse_inbound_event(
        self, payload: Payload, headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None: ...

    async def parse_inbound_interaction(
        self, payload: Payload, headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        """A tap or form submission, or None when the payload is not one."""
        ...

    async def parse_inbound_lifecycle(
        self, payload: Payload, headers: dict[str, str] | None = None
    ) -> ParsedSurfaceLifecycleEvent | None:
        """An event about the app itself -- added to a channel, home opened."""
        ...

    def parse_group_updates(
        self, payload: dict[str, object]
    ) -> list[ParsedGroupUpdate]:
        """What happened to groups themselves; empty on a bot that creates none."""
        ...

    def parse_line_changes(self, payload: dict[str, object]) -> list[ParsedLineChange]:
        """Messages heard that the platform says were edited or deleted."""
        ...

    def mention_is_provisional(self, event: ParsedInboundSurfaceEvent) -> bool:
        """Whether a group message's ``mentioned_agent`` waits on knowing which
        bot this is -- settled by `InboundReader.settle_mention` before anything
        decides whether the message is for us."""
        ...

    def unresolved_sender_reply(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, Payload] | None:
        """(message, reply_metadata) for an unresolved sender; None for the
        default sign-up prompt."""
        ...

    def linked_sender_confirmation(
        self, event: ParsedInboundSurfaceEvent
    ) -> tuple[str, Payload] | None:
        """A reply to send instead of a chat when the event only completed an
        identity-linking step."""
        ...


class InboundReader(Protocol):
    """What an inbound event did not carry, read with the surface's credentials."""

    async def enrich_inbound_event(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent | None:
        """The event with what its webhook left out filled in -- an email's body
        -- or None when there is nothing to act on."""
        ...

    async def settle_mention(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        bot_user_id: str | None,
    ) -> ParsedInboundSurfaceEvent:
        """The group message with ``mentioned_agent`` meaning *this* bot.

        A parser sees who was mentioned but not which bot it is; this knows,
        from the credentials or ``bot_user_id``. Unchanged where the parser's
        answer already means this bot.
        """
        ...

    async def fetch_sender_profile(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None: ...

    async def fetch_thread_context(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """The last few messages of the thread, for continuity on a mention."""
        ...

    async def download_attachment(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        attachment: Payload,
    ) -> tuple[bytes, str, str] | None:
        """(content, file_name, mime_type) of an inbound attachment."""
        ...


class OutboundDelivery(Protocol):
    """Everything that reaches the person."""

    async def deliver(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        envelope: SurfaceEnvelope,
        metadata: OutboundMetadata | None = None,
    ) -> DeliveryReceipt:
        """The one outbound seam for conversation content.

        Every kind of content is a field on the envelope, and the receipt says
        how each part landed -- natively, degraded to text or a link, or
        reaching nobody. The ``_render_*`` hooks it composes are a platform's
        private half of this call and deliberately not declared here.
        """
        ...

    async def send_message(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: OutboundMetadata | None = None,
    ) -> str | None:
        """The text primitive `deliver` degrades onto, and the only way to say
        something before a conversation exists. The platform's id for what it
        sent (the first message of a long one), when it names one: what a
        later status report about it, or a reply in its thread, refers to."""
        ...

    async def add_processing_indicator(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        metadata: OutboundMetadata | None = None,
    ) -> None: ...

    async def stream_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: ProgressHandle | None = None,
        metadata: OutboundMetadata | None = None,
    ) -> ProgressHandle | None:
        """Show live progress where a message can be edited; the handle to pass
        back next time. None: no editable progress here, keep to typing."""
        ...

    async def append_stream_text(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None,
        text: str,
        metadata: OutboundMetadata | None = None,
    ) -> StreamAppendResult:
        """Append model text to a live stream (Slack); not appended elsewhere."""
        ...

    async def finish_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None,
        message: str,
        metadata: OutboundMetadata | None = None,
    ) -> bool:
        """Close a live stream *with* the answer. False: not delivered, so the
        caller clears progress and sends the answer on its own."""
        ...

    async def end_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None = None,
    ) -> None:
        """Clean up a progress message that will not carry the answer."""
        ...

    async def acknowledge_interaction(
        self,
        *,
        credentials: SurfaceCredentials,
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None: ...

    async def retire_decision_card(
        self,
        *,
        credentials: SurfaceCredentials,
        handle: DecisionCardHandle,
        reason: CardRetirement = CardRetirement.SUPERSEDED,
    ) -> None:
        """Take the buttons off a card whose pause is over -- cancelled by a
        new message or answered by a typed one. A no-op where the platform
        keeps no handle to edit by."""
        ...

    async def set_thread_title(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        """Name a brand-new thread, where the platform has names. Best-effort."""
        ...

    async def send_cold_email(
        self,
        *,
        credentials: SurfaceCredentials,
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: OutboundMetadata | None = None,
    ) -> ColdEmailSendResult | None:
        """Start a thread with somebody who has never written. None on every
        platform but mail."""
        ...


class InChatSetup(Protocol):
    """Configuring the app from inside the chat. Optional; Slack only today.

    Checked with `in_chat_setup`, never called blind: a platform without it
    simply has no in-chat set-up, and an inert default on every adapter was how
    a missing implementation went unnoticed.
    """

    async def parse_channel_setup(
        self, payload: Payload, headers: dict[str, str] | None = None
    ) -> ChannelSetupRequest | None:
        """A payload of the set-up flow, or None for an ordinary message."""
        ...

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
    ) -> bool: ...

    async def open_channel_setup_modal(
        self,
        *,
        credentials: SurfaceCredentials,
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_name: str,
        surface_id: str | None = None,
    ) -> bool: ...

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
    ) -> bool: ...

    async def send_starter_prompt(
        self, *, credentials: SurfaceCredentials, user_id: str, prompt: str
    ) -> bool: ...

    async def channel_name(
        self, *, credentials: SurfaceCredentials, channel_id: str
    ) -> str | None: ...

    async def list_channels(
        self, *, credentials: SurfaceCredentials
    ) -> list[SurfaceChannelInfo]:
        """Channels the bot can be pointed at, for the web UI's picker."""
        ...


class ReplyWindowReopener(Protocol):
    """Asking somebody to write again once the platform's reply window has
    closed. Optional; WhatsApp only, where an approved template is the one
    message a business may start after 24 hours of silence.

    Checked with `reply_window_reopener`, like `InChatSetup`.
    """

    async def reopen_reply_window(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        template: ReengageTemplate,
    ) -> None:
        """Send ``template`` to the person ``event`` is from. Raises when it
        could not go: the caller holds a message on the strength of it."""
        ...


class PlacePrivacyReader(Protocol):
    """Asking the platform whether a place is kept to its members. Optional;
    Slack (``conversations.info``) and Teams (Graph's ``membershipType``),
    whose events do not always say.

    Checked with `place_privacy_reader`, like `InChatSetup`.
    """

    async def read_place_privacy(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> PlaceFacts | None:
        """What the platform says about the place ``event`` was said in, or
        None when it would not say -- which the caller reads as private."""
        ...


class SurfacePlatformAdapterPort(
    InboundParser, InboundReader, OutboundDelivery, Protocol
):
    """A platform adapter: what the registry holds."""


_IN_CHAT_SETUP_METHODS = tuple(
    name
    for name, value in vars(InChatSetup).items()
    if not name.startswith("_") and callable(value)
)


def _has_in_chat_setup(adapter: object) -> TypeIs[InChatSetup]:
    # Asked by name rather than with `isinstance`: a protocol check reads
    # attributes statically, which a stand-in adapter built in a test cannot
    # answer, and the question is only ever "does it have these methods".
    return all(
        callable(getattr(adapter, name, None)) for name in _IN_CHAT_SETUP_METHODS
    )


def in_chat_setup(adapter: object) -> InChatSetup | None:
    """The adapter's in-chat set-up, or None where its platform has none."""
    return adapter if _has_in_chat_setup(adapter) else None


def _reopens_reply_window(adapter: object) -> TypeIs[ReplyWindowReopener]:
    return callable(getattr(adapter, "reopen_reply_window", None))


def reply_window_reopener(adapter: object) -> ReplyWindowReopener | None:
    """The adapter's way to reopen a closed window, or None where it has none."""
    return adapter if _reopens_reply_window(adapter) else None


def _reads_place_privacy(adapter: object) -> TypeIs[PlacePrivacyReader]:
    return callable(getattr(adapter, "read_place_privacy", None))


def place_privacy_reader(adapter: object) -> PlacePrivacyReader | None:
    """The adapter's privacy lookup, or None where its platform has none."""
    return adapter if _reads_place_privacy(adapter) else None


__all__ = [
    "InChatSetup",
    "PlacePrivacyReader",
    "InboundParser",
    "InboundReader",
    "OutboundDelivery",
    "ReplyWindowReopener",
    "SurfacePlatformAdapterPort",
    "in_chat_setup",
    "place_privacy_reader",
    "reply_window_reopener",
]

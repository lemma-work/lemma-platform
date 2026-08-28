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
from app.modules.agent_surfaces.platforms.chrome import SurfaceChromeMixin
from app.modules.agent_surfaces.platforms.envelope_delivery import (
    EnvelopeDeliveryMixin,
)
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
    StreamAppendResult,
    SurfaceApprovalRenderPlan,
    SurfaceContextMessage,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)


class BaseSurfaceAdapter(EnvelopeDeliveryMixin, SurfaceChromeMixin):
    platform: str

    def split_inbound_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """One webhook delivery, as the one-or-more messages it actually carries.

        Every parser here reads a single message out of a delivery, which is
        right for platforms that send one. Where a platform may batch, silently
        parsing the first and discarding the rest loses a person's message with
        nothing logged — so the platform that batches says so here, and the
        webhook handler processes each part as its own inbound event.

        Default: the delivery is the message.
        """
        return [payload]

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

    async def _render_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Show a resource as the platform's own card. Default: no cards, so say
        what it is in words and report the degradation.

        Returning ``False`` is the whole point of the bool. This default already
        delivered the text, but the caller recorded ``NATIVE`` because nothing
        raised -- so ``receipt.degraded`` could never name a resource, on any
        platform, and "shown as a card" was indistinguishable from "described in
        a sentence"."""
        await self.send_message(
            credentials=credentials,
            event=event,
            message=render_plan.to_plain_text(),
            metadata=metadata,
        )
        return False

    async def _render_choices(
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

    async def _render_decision(
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

    async def _render_voice(
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
        """Deliver a file's bytes natively on the platform.

        Default: platform has no native file send → return False so the caller
        falls back to sending a Lemma app deep link (not a public download
        URL — it only opens for a recipient with pod access).
        """
        del credentials, event, file_name, file_bytes, mime_type, caption
        return False

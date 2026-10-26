from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    WhatsAppReplyTarget,
    reply_target_as,
    reply_target_for,
)

from typing import Any


from app.core.log.log import get_logger
from app.modules.agent.contracts import ToolContext
import httpx

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    WhatsAppSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import note_partial_delivery
from app.modules.agent_surfaces.platforms.send_guard import unsendable
from app.modules.agent_surfaces.platforms.whatsapp import media
from app.modules.agent_surfaces.platforms.whatsapp.indicators import (
    acknowledge_inbound,
)
from app.core.config import reveal_secret
from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    WhatsAppCredentials,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
    resolve_api_base,
)
from app.modules.agent_surfaces.platforms.whatsapp.models import (
    WhatsAppFileAttachment,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    WhatsAppRecipient,
    retry_notice_interactive,
    flow_with_message,
    whatsapp_message_bodies,
    whatsapp_text_payload,
    truncate_whatsapp_text,
)
from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    to_whatsapp_text,
)

from app.modules.agent_surfaces.platforms.whatsapp.interactive import (
    WhatsAppInteractiveRenders,
    recipient_of,
)

logger = get_logger(__name__)


class WhatsAppPlatformService(WhatsAppInteractiveRenders):
    def __init__(
        self, credentials: CredentialsLike, *, client: WhatsAppClient | None = None
    ):
        self.credentials = WhatsAppCredentials.of(credentials)
        # Revealed into a service built for the call at hand.
        self._access_token = reveal_secret(self.credentials.access_token) or ""
        self._phone_number_id = self.credentials.phone_number_id or ""
        # Resolve the base here (honoring a credential override) and hand it to
        # the typed client so all transport goes through one place.
        self._api_base = resolve_api_base(self.credentials)
        self._client = client or WhatsAppClient(
            access_token=self._access_token,
            phone_number_id=self._phone_number_id,
            api_base=self._api_base,
        )

    def _arrival_number(self, event: ParsedInboundSurfaceEvent) -> str:
        """The number to send from: the one the message arrived on, else ours."""
        return (
            reply_target_as(event, WhatsAppReplyTarget).phone_number_id
            or self._phone_number_id
        )

    async def fetch_sender_profile(
        self, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return SurfaceSenderProfile(
            phone=event.sender_phone,
            display_name=event.sender_display_name,
        )

    async def get_display_phone_number(self) -> str | None:
        """Return the human-messageable WhatsApp number for this phone_number_id.

        ``phone_number_id`` is Meta's opaque Graph id; users need the display
        phone number in surfaces list UI. Prefer already-stored account
        credential metadata, then resolve it through Graph best-effort.
        """
        if self.credentials.display_phone_number:
            return self.credentials.display_phone_number
        try:
            return await self._client.get_phone_number_field("display_phone_number")
        except Exception:
            # `info`, not `warning`: the caller has a usable answer without it.
            # But with the exception attached and above the deployment's
            # `LOG_LEVEL=INFO`, because a lookup that quietly returns None is
            # the kind of thing somebody later has to explain.
            logger.info(
                "agent_surfaces.service.whatsapp_display_phone_lookup_phone.observed",
                phone_number_id=self._phone_number_id,
                exc_info=True,
            )
            return None

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Send the message; the id of its first part, which is what Meta's
        later status report about it names."""
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if recipient is None or not phone_number_id or not self._access_token:
            if (metadata or {}).get("private_onboarding"):
                raise RuntimeError("WhatsApp cannot deliver private onboarding")
            raise unsendable(
                "WhatsApp",
                access_token=self._access_token,
                phone_number_id=phone_number_id,
                recipient=recipient.to if recipient else None,
            )

        bodies = whatsapp_message_bodies(message)
        interactive = _interactive_reply(event, message, bodies, metadata, recipient)
        if interactive is not None:
            return await self._client.send_interactive(
                phone_number_id=phone_number_id,
                to=recipient.to,
                interactive=interactive,
            )
        first: str | None = None
        for index, body in enumerate(bodies):
            try:
                message_id = await self._client.send_message_payload(
                    phone_number_id=phone_number_id,
                    payload={
                        "messaging_product": "whatsapp",
                        "recipient_type": recipient.recipient_type,
                        "to": recipient.to,
                        "type": "text",
                        "text": {"body": body},
                    },
                )
                first = first or message_id
            except platform_transport_errors():
                if index:
                    # The person already has the first part. The caller sees
                    # this as "not delivered" and there is no rung below plain
                    # text to fall back to, so what it cannot say is that half
                    # the answer is on their phone.
                    note_partial_delivery()
                    logger.warning(
                        "agent_surfaces.service.whatsapp_message_partially_delivered.degraded",
                        sent_parts=index,
                        total_parts=len(bodies),
                        exc_info=True,
                    )
                raise
        return first

    async def acknowledge_interaction(
        self,
        interaction: ParsedSurfaceInteraction,
        *,
        text: str | None,
        show_alert: bool,
        clear_actions: bool,
    ) -> None:
        """Say something back, when it is worth a whole new message.

        WhatsApp has no edit API and no callback answer, so an acknowledgement
        can only be another message in the person's chat. Tapping a button
        already posts their choice there, so confirming a settled decision adds
        a line that says what the line above it says -- the same reasoning that
        makes this platform's progress updates rationed rather than live.

        ``show_alert`` marks the outcomes they cannot infer from their own tap:
        the action expired, the conversation moved on, or it could not be
        completed. Those are said. A plain "Done" is not.
        """
        del clear_actions
        note = (text or "").strip()
        if not note or not show_alert:
            return
        reply_target = reply_target_for("WHATSAPP", interaction.reply_target)
        if not isinstance(reply_target, WhatsAppReplyTarget):
            return
        sender_wa_id = reply_target.sender_wa_id
        # The number it arrived on wins over the one this adapter was configured
        # with, exactly as `stream_progress` and the send paths do it. With a
        # pool the two differ, and the configured one is a number the person has
        # never written to -- so the acknowledgement for their own tap arrives
        # from somewhere else, outside the thread they are looking at.
        phone_number_id = reply_target.phone_number_id or self._phone_number_id
        if not sender_wa_id or not phone_number_id or not self._access_token:
            return
        try:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload={
                    "messaging_product": "whatsapp",
                    "to": sender_wa_id,
                    "type": "text",
                    "text": {"body": truncate_whatsapp_text(note, 1024)},
                },
            )
        except WhatsAppApiError, httpx.HTTPError:
            # Nothing recovers this one: the acknowledgement simply does not
            # appear, and the person is left looking at a button they pressed.
            # It was `debug` with no exception attached, which at the
            # deployment's `LOG_LEVEL=INFO` is no record at all -- and
            # `WhatsAppApiError` carries Meta's own body excerpt, which is the
            # only thing that says *why*.
            logger.warning(
                "agent_surfaces.service.whatsapp_interaction_acknowledgement_failed.degraded",
                exc_info=True,
            )

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
    ) -> None:
        """Post a progress update as its own message.

        WhatsApp has no message-edit API, so unlike Telegram or Teams there is no
        live message to rewrite — an update can only be a new message in the
        person's chat. The observer is what keeps that from becoming spam: it
        rations these to a moved plan or one "still going" per run. This end just
        sends what it is given, best-effort, so a failed update cannot touch the
        run.
        """
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if recipient is None or not phone_number_id or not self._access_token:
            return
        body = to_whatsapp_text(progress_text)
        if not body:
            return
        await self._client.send_message_payload(
            phone_number_id=phone_number_id,
            payload=whatsapp_text_payload(
                recipient_wa_id=recipient.to,
                recipient_type=recipient.recipient_type,
                body=body,
                preview_url=False,
            ),
        )

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Blue read ticks and a typing bubble on the inbound message."""
        phone_number_id = self._arrival_number(event)
        if not phone_number_id or not self._access_token:
            return
        await acknowledge_inbound(
            self._client,
            phone_number_id=phone_number_id,
            recipient=recipient_of(event),
            message_id=str(event.external_message_id or "").strip(),
            is_refresh=bool((metadata or {}).get("is_refresh")),
        )

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound WhatsApp attachment."""
        del event
        return await media.download_attachment(self._client, attachment)

    async def send_file_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Upload + send raw file bytes to the inbound chat (egress).

        Returns True on success; False so the caller falls back to a URL link.
        """
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if not self._access_token or not phone_number_id or recipient is None:
            return False
        return await media.send_file(
            self._client,
            phone_number_id=phone_number_id,
            recipient_wa_id=recipient.to,
            recipient_type=recipient.recipient_type,
            file_name=file_name,
            file_bytes=file_bytes,
            mime_type=mime_type,
            caption=caption,
        )

    def _whatsapp_metadata(
        self,
        ctx: ToolContext,
    ) -> WhatsAppSurfaceEventMetadata | None:
        metadata = ctx.surface_metadata
        if isinstance(metadata, WhatsAppSurfaceEventMetadata):
            return metadata
        return None

    def _current_message_attachments(
        self,
        ctx: ToolContext,
    ) -> list[WhatsAppFileAttachment]:
        metadata = self._whatsapp_metadata(ctx)
        if metadata is None:
            return []
        return common.coerce_attachments(metadata.attachments, WhatsAppFileAttachment)

    def _resolve_phone_number_id(self, ctx: ToolContext) -> str | None:
        metadata = self._whatsapp_metadata(ctx)
        return (
            (metadata.phone_number_id if metadata is not None else None)
            or ctx.external_channel_id
            or self._phone_number_id
            or None
        )

    def _resolve_recipient_wa_id(self, ctx: ToolContext) -> str | None:
        metadata = self._whatsapp_metadata(ctx)
        if metadata is not None:
            for contact in metadata.contacts:
                if isinstance(contact, dict):
                    wa_id = str(contact.get("wa_id") or "").strip()
                    if wa_id:
                        return wa_id
        thread_id = str(ctx.external_thread_id or "")
        if "@" in thread_id:
            candidate = thread_id.split("@", 1)[0].strip()
            if candidate:
                return candidate
        return None


def _interactive_reply(
    event: ParsedInboundSurfaceEvent,
    message: str,
    bodies: list[str],
    metadata: dict[str, object] | None,
    recipient: WhatsAppRecipient,
) -> dict[str, object] | None:
    """The message as an interactive one, when it is an onboarding step or a
    failure notice with a Try again button; None to send it as text."""
    flow = (metadata or {}).get("onboarding_flow")
    if event.is_dm and flow and not recipient.is_group:
        return flow_with_message(flow, message)
    return retry_notice_interactive(bodies, metadata, recipient)

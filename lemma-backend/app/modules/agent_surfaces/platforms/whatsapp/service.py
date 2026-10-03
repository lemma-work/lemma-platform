from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai.tools import RunContext

from app.core.log.log import get_logger
from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.domain.models import SurfaceSenderProfile
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    WhatsAppSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.envelope_delivery import (
    PartialTextDelivery,
)
from app.modules.agent_surfaces.platforms.send_guard import unsendable
from app.modules.agent_surfaces.platforms.whatsapp import media
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
    resolve_api_base,
)
from app.modules.agent_surfaces.platforms.whatsapp.interactive_sends import (
    WhatsAppInteractiveSends,
    recipient_of,
)
from app.modules.agent_surfaces.platforms.whatsapp.models import (
    WhatsAppFileAttachment,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    WhatsAppRecipient,
    flow_with_message,
    whatsapp_message_bodies,
    whatsapp_text_payload,
    truncate_whatsapp_text,
)
from app.modules.agent_surfaces.platforms.whatsapp.text_format import (
    to_whatsapp_text,
)

logger = get_logger(__name__)


class WhatsAppPlatformService(WhatsAppInteractiveSends):
    def __init__(
        self, credentials: dict[str, Any], *, client: WhatsAppClient | None = None
    ):
        self.credentials = credentials
        self._access_token = credentials.get("access_token") or ""
        self._phone_number_id = credentials.get("phone_number_id") or ""
        # Resolve the base here (honoring a credential override) and hand it to
        # the typed client so all transport goes through one place.
        self._api_base = resolve_api_base(credentials)
        self._client = client or WhatsAppClient(
            access_token=self._access_token,
            phone_number_id=self._phone_number_id,
            api_base=self._api_base,
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
        for key in ("display_phone_number", "phone_number"):
            value = str(self.credentials.get(key) or "").strip()
            if value:
                return value
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
    ) -> None:
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

        flow = (metadata or {}).get("onboarding_flow")
        if event.is_dm and flow and not recipient.is_group:
            await self._client.send_interactive(
                phone_number_id=phone_number_id,
                to=recipient.to,
                interactive=flow_with_message(flow, message),
            )
            return
        await self._send_text_parts(
            event, phone_number_id, recipient, whatsapp_message_bodies(message)
        )

    async def _send_text_parts(
        self,
        event: ParsedInboundSurfaceEvent,
        phone_number_id: str,
        recipient: WhatsAppRecipient,
        bodies: list[str],
    ) -> None:
        """Send a reply split into parts, saying how far it got if it stops."""
        if not bodies:
            # Formatting can leave nothing (a message that was only a rule or
            # an empty bullet). Returning quietly reported it as delivered.
            raise AgentSurfacePlatformError(
                "WHATSAPP", "the message was empty once formatted; nothing was sent."
            )
        # In a group the answer quotes what it answers: several people may be
        # talking to the bot at once, and an unquoted reply is anyone's.
        quote = (
            str(event.external_message_id or "").strip() if recipient.is_group else ""
        )
        for index, body in enumerate(bodies):
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": recipient.recipient_type,
                "to": recipient.to,
                "type": "text",
                "text": {"body": body},
            }
            try:
                await self._send_quoting(
                    phone_number_id, payload, quote=quote if not index else ""
                )
            except PLATFORM_TRANSPORT_ERRORS as exc:
                if not index:
                    raise
                # The person already has the first part. Raising the plain
                # error made the caller treat the whole answer as undelivered,
                # and a fallback then resent what they had already read.
                logger.warning(
                    "agent_surfaces.service.whatsapp_message_partially_delivered.degraded",
                    sent_parts=index,
                    total_parts=len(bodies),
                    exc_info=True,
                )
                raise PartialTextDelivery(
                    "WHATSAPP", sent_parts=index, total_parts=len(bodies)
                ) from exc

    async def _send_quoting(
        self, phone_number_id: str, payload: dict[str, object], *, quote: str
    ) -> None:
        """Send ``payload`` as a reply to message ``quote``, or plainly.

        Meta documents ``context`` for one-to-one chats and says nothing of
        groups. A refusal is therefore read as the quote being unwelcome rather
        than the message: the same body goes once more without it. A 5xx or a
        429 may mean it was delivered, so those are not repeated.
        """
        if not quote:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id, payload=payload
            )
            return
        try:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload={**payload, "context": {"message_id": quote}},
            )
        except WhatsAppApiError as exc:
            if exc.status_code >= 500 or exc.status_code == 429:
                raise
            logger.warning(
                "agent_surfaces.service.whatsapp_group_quote_rejected.degraded",
                status_code=exc.status_code,
                meta_code=exc.meta_code,
                exc_info=True,
            )
            await self._client.send_message_payload(
                phone_number_id=phone_number_id, payload=payload
            )

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
        reply_target = interaction.reply_target or {}
        sender_wa_id = reply_target.get("sender_wa_id")
        # The number it arrived on wins over the one this adapter was configured
        # with, exactly as `stream_progress` and the send paths do it. With a
        # pool the two differ, and the configured one is a number the person has
        # never written to -- so the acknowledgement for their own tap arrives
        # from somewhere else, outside the thread they are looking at.
        phone_number_id = reply_target.get("phone_number_id") or self._phone_number_id
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
        """Acknowledge the inbound message with blue read ticks + a typing bubble.

        WhatsApp couples mark-as-read and the typing indicator into a single
        ``status:read`` call keyed by the inbound message id. The typing bubble
        shows for ~25s or until the next message is sent, so a single call at run
        start is enough (WhatsApp has no message-edit API for per-step progress).
        Best-effort: an indicator failure never affects the run. When the inbound
        message id is missing we fall back to the legacy 💬 reaction — but only
        on the opening call. The bubble expires after ~25s so the observer
        refreshes it on a timer, and a reaction re-posted on every tick would be
        an API call every twenty seconds to say something already said.
        """
        is_refresh = bool((metadata or {}).get("is_refresh"))
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        message_id = str(event.external_message_id or "").strip()
        if not phone_number_id or not self._access_token:
            return
        if recipient is not None and recipient.is_group:
            # Meta documents neither a read receipt, a typing bubble nor a
            # reaction from a business in a group -- its November 2025 guide
            # listed marking read as unsupported there. Nothing is sent rather
            # than a call a group may refuse on every turn.
            return
        sender_wa_id = recipient.to if recipient is not None else None

        if message_id:
            try:
                await self._client.mark_read_and_typing(
                    phone_number_id=phone_number_id,
                    message_id=message_id,
                )
                return
            except Exception:
                # Best-effort indicator, and the reaction fallback below still
                # runs, so this is `info` rather than `warning`. It said "log at
                # debug so it is diagnosable without spamming warnings" -- and
                # the deployment runs `LOG_LEVEL=INFO`, where `debug` is not
                # diagnosable, it is absent. The exception goes with it.
                logger.info(
                    "agent_surfaces.service.whatsapp_mark_read_typing_best.observed",
                    exc_info=True,
                )

        # Fallback: no inbound id (or read/typing rejected) — post a reaction so
        # the user still sees the agent acknowledged the message.
        if is_refresh or not sender_wa_id or not message_id:
            return
        try:
            await self._client.react(
                phone_number_id=phone_number_id,
                to=sender_wa_id,
                message_id=message_id,
                emoji="\U0001f4ac",
            )
        except Exception:
            # The last rung of the acknowledgement ladder: read receipt, then
            # typing, then this. Nothing follows it, so the person sees no
            # acknowledgement at all -- worth a record, with the reason.
            logger.info(
                "agent_surfaces.service.whatsapp_reaction_indicator_best_effort.observed",
                exc_info=True,
            )

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound WhatsApp attachment (no RunContext)."""
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
        """Upload + send raw file bytes to the inbound chat (egress, no RunContext).

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

    async def send_voice_note(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        audio_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Send audio as a voice note; False so the caller sends it as a file.

        A WhatsApp audio message takes no caption, and dropping it would lose
        the words the note was sent with. They follow as a message of their
        own -- best-effort, because the note itself has already arrived.
        """
        phone_number_id = self._arrival_number(event)
        recipient = recipient_of(event)
        if not self._access_token or not phone_number_id or recipient is None:
            return False
        sent = await media.send_voice(
            self._client,
            phone_number_id=phone_number_id,
            recipient_wa_id=recipient.to,
            recipient_type=recipient.recipient_type,
            file_name=file_name,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
        )
        if sent and caption and caption.strip():
            try:
                await self.send_message(event, caption)
            except PLATFORM_TRANSPORT_ERRORS:
                logger.warning(
                    "agent_surfaces.service.whatsapp_voice_caption_failed.degraded",
                    exc_info=True,
                )
        return sent

    def _whatsapp_metadata(
        self,
        ctx: RunContext[ConversationContext],
    ) -> WhatsAppSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, WhatsAppSurfaceEventMetadata):
            return metadata
        return None

    def _current_message_attachments(
        self,
        ctx: RunContext[ConversationContext],
    ) -> list[WhatsAppFileAttachment]:
        metadata = self._whatsapp_metadata(ctx)
        if metadata is None:
            return []
        return common.coerce_attachments(metadata.attachments, WhatsAppFileAttachment)

    def _resolve_phone_number_id(
        self, ctx: RunContext[ConversationContext]
    ) -> str | None:
        metadata = self._whatsapp_metadata(ctx)
        return (
            (metadata.phone_number_id if metadata is not None else None)
            or ctx.deps.external_channel_id
            or self._phone_number_id
            or None
        )

    def _resolve_recipient_wa_id(
        self, ctx: RunContext[ConversationContext]
    ) -> str | None:
        metadata = self._whatsapp_metadata(ctx)
        if metadata is not None:
            for contact in metadata.contacts:
                if isinstance(contact, dict):
                    wa_id = str(contact.get("wa_id") or "").strip()
                    if wa_id:
                        return wa_id
        thread_id = str(ctx.deps.external_thread_id or "")
        if "@" in thread_id:
            candidate = thread_id.split("@", 1)[0].strip()
            if candidate:
                return candidate
        return None

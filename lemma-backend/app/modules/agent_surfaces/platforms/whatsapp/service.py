from __future__ import annotations

import mimetypes
from typing import Any

from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    WhatsAppSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.models import (
    WhatsAppCurrentContactParams,
    WhatsAppCurrentContactResult,
    WhatsAppFileAttachment,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Separator for encoding ask_user routing into a WhatsApp button/list ``id``
# (``callback_id~header~value``). The callback id itself uses ``|``, so ``~``
# unambiguously splits the three parts. WhatsApp allows ids up to 256 chars.
WHATSAPP_INTERACTION_SEP = "~"

# Sentinel used in place of a question ``header`` to mark an approval button
# reply (``callback_id~__approval__~<decision>``). The parser routes this to an
# approval decision instead of an ask_user answer.
WHATSAPP_APPROVAL_HEADER = "__approval__"


def _build_whatsapp_interactive(
    callback_id: str, question: SurfaceQuestion
) -> dict[str, Any] | None:
    """Build a WhatsApp interactive payload for one question, or ``None`` if it
    can't be expressed natively (id over 256 chars, more than 10 options, or a
    header containing the reserved separator)."""
    # The reply id packs ``callback_id~header~value`` and is decoded with a
    # 2-split, so the value may contain ``~`` but the header must not — otherwise
    # the split misassigns and the answer is mis-keyed. Fall back to text when a
    # header contains the separator (rare; header is model-authored).
    if WHATSAPP_INTERACTION_SEP in (question.header or ""):
        return None
    rows: list[tuple[str, str]] = []
    for option in question.options:
        button_id = (
            f"{callback_id}{WHATSAPP_INTERACTION_SEP}{question.header}"
            f"{WHATSAPP_INTERACTION_SEP}{option.label}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        rows.append((button_id, option.label))
    body = {"text": (question.question or "").strip()[:1024] or "Please choose"}
    if 1 <= len(rows) <= 3:
        return {
            "type": "button",
            "body": body,
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": rid, "title": title[:20]}}
                    for rid, title in rows
                ]
            },
        }
    if 4 <= len(rows) <= 10:
        return {
            "type": "list",
            "body": body,
            "action": {
                "button": "Choose",
                "sections": [
                    {"rows": [{"id": rid, "title": title[:24]} for rid, title in rows]}
                ],
            },
        }
    return None


def _build_whatsapp_approval_interactive(
    plan: SurfaceApprovalRenderPlan,
) -> dict[str, Any] | None:
    """Build a WhatsApp reply-button payload for an approval prompt, or ``None``
    if it can't be expressed natively (more than 3 buttons, or an id over 256
    chars). Each button id packs ``callback_id~__approval__~<decision>``."""
    buttons: list[dict[str, Any]] = []
    for button in plan.buttons:
        button_id = (
            f"{plan.callback_id}{WHATSAPP_INTERACTION_SEP}{WHATSAPP_APPROVAL_HEADER}"
            f"{WHATSAPP_INTERACTION_SEP}{button.decision}"
        )
        if len(button_id.encode("utf-8")) > 256:
            return None
        buttons.append(
            {"type": "reply", "reply": {"id": button_id, "title": button.label[:20]}}
        )
    if not 1 <= len(buttons) <= 3:
        return None
    body_parts = [f"*{plan.title}*"]
    if plan.reason:
        body_parts.append(plan.reason)
    if plan.action_summary:
        body_parts.append(f"Action: {plan.action_summary}")
    body_text = "\n\n".join(body_parts).strip()[:1024] or "Approval needed"
    return {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": buttons},
    }


_WHATSAPP_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppPlatformService:
    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials
        self._access_token = credentials.get("access_token") or ""
        self._phone_number_id = credentials.get("phone_number_id") or ""
        # Resolve the base here (honoring a credential override, else the module
        # constant that tests monkeypatch) and hand it to the typed client so all
        # transport goes through one place.
        self._api_base = credentials.get("api_base_url") or _WHATSAPP_API_BASE
        self._client = WhatsAppClient(
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
            logger.debug(
                "agent_surfaces.service.whatsapp_display_phone_lookup_phone.observed",
                phone_number_id=self._phone_number_id,
            )
            return None

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        sender_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        if not sender_wa_id or not phone_number_id or not self._access_token:
            logger.debug(
                'agent_surfaces.service.whatsapp_send_message_skipped_due.diagnostic',
                phone_number_id=phone_number_id,
                sender_wa_id=sender_wa_id,
            )
            return

        await self._client.send_message_payload(
            phone_number_id=phone_number_id,
            payload={
                "messaging_product": "whatsapp",
                "to": sender_wa_id,
                "type": "text",
                "text": {"body": message},
            },
        )

    async def send_questions(
        self,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render ask_user questions as native interactive replies.

        ≤3 options → reply buttons, 4–10 → a list; multi-select or anything that
        can't be encoded returns ``False`` so the caller falls back to text. The
        button/list ``id`` carries ``callback_id~header~value`` (no token store —
        WhatsApp ids allow 256 chars).
        """
        del metadata
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        sender_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        if not sender_wa_id or not phone_number_id or not self._access_token:
            # Missing credentials/target — the caller's text fallback hits the same
            # guard in send_message, so log here to make the double-skip diagnosable
            # instead of a silent swallow.
            logger.debug(
                'agent_surfaces.service.whatsapp_send_questions_skipped_missing.diagnostic',
                phone_number_id=phone_number_id,
                sender_wa_id=sender_wa_id,
            )
            return False
        if any(q.multi_select for q in question_plan.questions):
            return False
        interactives = []
        for question in question_plan.questions:
            interactive = _build_whatsapp_interactive(
                question_plan.callback_id, question
            )
            if interactive is None:
                return False
            interactives.append(interactive)
        for interactive in interactives:
            await self._client.send_interactive(
                phone_number_id=phone_number_id,
                to=sender_wa_id,
                interactive=interactive,
            )
        return True

    async def send_approval(
        self,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as WhatsApp reply buttons.

        Approve/Deny (and optionally Approve-for-session) render as ≤3 reply
        buttons; the tapped button's id carries the decision. Returns ``False``
        (caller falls back to text) when the buttons can't be encoded natively.
        """
        del metadata
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        sender_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        if not sender_wa_id or not phone_number_id or not self._access_token:
            return False
        interactive = _build_whatsapp_approval_interactive(approval_plan)
        if interactive is None:
            return False
        await self._client.send_interactive(
            phone_number_id=phone_number_id,
            to=sender_wa_id,
            interactive=interactive,
        )
        return True

    async def send_display_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        sender_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        if not phone_number_id or not sender_wa_id:
            logger.debug(
                'agent_surfaces.service.whatsapp_send_display_resource_skipped.diagnostic',
                phone_number_id=phone_number_id,
            )
            return
        action = render_plan.primary_action
        if action is None:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=_whatsapp_text_payload(
                    recipient_wa_id=sender_wa_id,
                    body=_whatsapp_display_resource_text(render_plan),
                    preview_url=False,
                ),
            )
            return

        try:
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=_whatsapp_cta_url_payload(
                    recipient_wa_id=sender_wa_id,
                    render_plan=render_plan,
                ),
            )
        except WhatsAppApiError:
            logger.debug(
                "agent_surfaces.service.whatsapp_display_resource_cta_url.observed"
            )
            await self._client.send_message_payload(
                phone_number_id=phone_number_id,
                payload=_whatsapp_text_payload(
                    recipient_wa_id=sender_wa_id,
                    body=_whatsapp_display_resource_text(render_plan),
                    preview_url=True,
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
        message id is missing we fall back to the legacy 💬 reaction.
        """
        del metadata
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        sender_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        message_id = str(event.external_message_id or "").strip()
        if not phone_number_id or not self._access_token:
            return

        if message_id:
            try:
                await self._client.mark_read_and_typing(
                    phone_number_id=phone_number_id,
                    message_id=message_id,
                )
                return
            except Exception:
                # Best-effort indicator; log at debug so it is diagnosable without
                # spamming warnings, then fall through to the reaction fallback.
                logger.debug(
                    "agent_surfaces.service.whatsapp_mark_read_typing_best.observed"
                )

        # Fallback: no inbound id (or read/typing rejected) — post a reaction so
        # the user still sees the agent acknowledged the message.
        if not sender_wa_id or not message_id:
            return
        try:
            await self._client.react(
                phone_number_id=phone_number_id,
                to=sender_wa_id,
                message_id=message_id,
                emoji="\U0001f4ac",
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.whatsapp_reaction_indicator_best_effort.observed"
            )

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound WhatsApp attachment (no RunContext)."""
        del event
        if not self._access_token:
            return None
        media_id = str(attachment.get("id") or "").strip()
        if not media_id:
            return None
        media_info = await self._client.get_media_info(media_id)
        if not media_info:
            return None
        download_url = str(media_info.get("url") or "").strip()
        if not download_url:
            return None
        file_name = (
            str(attachment.get("name") or "").strip()
            or _filename_from_url(download_url)
            or "whatsapp_file"
        )
        content = await self._client.download_media(download_url)
        mime_type = (
            str(
                attachment.get("mime_type") or media_info.get("mime_type") or ""
            ).strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

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

        Returns True on success; False (or on an unsupported-media error raised by
        the upload) so the caller falls back to a URL link.
        """
        phone_number_id = (
            event.reply_target.get("phone_number_id") or self._phone_number_id
        )
        recipient_wa_id = event.reply_target.get("sender_wa_id") or event.sender_phone
        if not self._access_token or not phone_number_id or not recipient_wa_id:
            return False
        send_type = _resolve_whatsapp_send_type(
            delivery_mode="auto", mime_type=mime_type
        )
        try:
            media_id = await self._client.upload_media(
                phone_number_id=phone_number_id,
                file_name=file_name,
                file_bytes=file_bytes,
                mime_type=mime_type,
            )
        except WhatsAppApiError as exc:
            # Unsupported media / rejected upload — caller falls back to a link.
            logger.debug(
                "surface.whatsapp.media_upload_rejected",
                mime_type=mime_type,
                status_code=exc.status_code,
                exc_info=True,
            )
            return False
        if not media_id:
            return False
        message_id = await self._client.send_media(
            phone_number_id=phone_number_id,
            to=recipient_wa_id,
            media_id=media_id,
            send_type=send_type,
            file_name=file_name,
            caption=caption,
        )
        return bool(message_id)

    async def get_current_contact(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: WhatsAppCurrentContactParams,
    ) -> WhatsAppCurrentContactResult:
        del request
        metadata = self._whatsapp_metadata(ctx)
        contacts = list(metadata.contacts) if metadata is not None else []
        first_contact = contacts[0] if contacts else {}
        display_name = None
        if isinstance(first_contact, dict):
            display_name = (first_contact.get("profile") or {}).get("name")
        attachment_names = [
            attachment.name
            for attachment in self._current_message_attachments(ctx)
            if attachment.name
        ]
        return WhatsAppCurrentContactResult(
            success=True,
            message="Resolved current WhatsApp contact details.",
            wa_id=self._resolve_recipient_wa_id(ctx),
            display_name=display_name,
            phone_number_id=self._resolve_phone_number_id(ctx),
            waba_id=metadata.waba_id if metadata is not None else None,
            attachment_names=attachment_names,
        )

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


def _resolve_whatsapp_send_type(*, delivery_mode: str, mime_type: str) -> str:
    requested = str(delivery_mode or "auto").lower()
    if requested != "auto":
        return requested
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


def _whatsapp_cta_url_payload(
    *,
    recipient_wa_id: str,
    render_plan: SurfaceDisplayRenderPlan,
) -> dict[str, Any]:
    action = render_plan.primary_action
    body = _truncate_whatsapp_text(
        _whatsapp_display_resource_text(render_plan, include_action=False),
        1024,
    )
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": _truncate_whatsapp_button_text(
                        action.label if action else "Open"
                    ),
                    "url": action.url if action else "",
                },
            },
        },
    }


def _whatsapp_text_payload(
    *,
    recipient_wa_id: str,
    body: str,
    preview_url: bool,
) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_wa_id,
        "type": "text",
        "text": {
            "body": _truncate_whatsapp_text(body, 4096),
            "preview_url": preview_url,
        },
    }


def _whatsapp_display_resource_text(
    render_plan: SurfaceDisplayRenderPlan,
    *,
    include_action: bool = True,
) -> str:
    parts = [f"*{render_plan.title}*"]
    if render_plan.summary:
        parts.append(render_plan.summary)
    parts.extend(render_plan.detail_lines[:5])
    action = render_plan.primary_action
    if include_action and action is not None:
        parts.append(f"{action.label}: {action.url}")
    return "\n\n".join(parts)


def _truncate_whatsapp_button_text(value: str) -> str:
    text = " ".join(str(value or "").split()) or "Open"
    return text if len(text) <= 20 else text[:19].rstrip() + "..."


def _truncate_whatsapp_text(value: str, max_length: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_length else text[: max_length - 1].rstrip() + "..."


def _filename_from_url(url: str) -> str:
    return str(url or "").rstrip("/").split("/")[-1].strip()

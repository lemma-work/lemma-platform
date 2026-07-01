"""Resend email surface operations (send + reply via the Resend API).

Resend is a system-credentialed email surface: outbound mail goes to the Resend
REST API, inbound mail arrives via a webhook (parsed by ``ResendInboundParser``).
Rendering and attachment handling reuse ``email_common`` so Resend behaves like
Gmail/Outlook for the agent, but over native HTTP rather than Composio.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from pydantic_ai.tools import RunContext

from app.core.log.log import get_logger
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    ResendSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.attachment_limits import attachment_cap
from app.modules.agent_surfaces.platforms.email_common import (
    append_attachment_links,
    coerce_display_resource_plans,
    render_email_content,
    reply_subject,
    resolve_outbound_email_attachments,
)
from app.modules.agent_surfaces.platforms.email_models import (
    ResendReplyEmailParams,
    ResendReplyEmailResult,
)

logger = get_logger(__name__)

_RESEND_API_BASE = "https://api.resend.com"


class ResendPlatformService:
    def __init__(self, credentials: dict[str, Any]):
        self._api_key = str(credentials.get("api_key") or "")
        self._from_address = str(credentials.get("from_address") or "")
        self._from_name = str(credentials.get("from_name") or "Lemma")
        self._api_base = str(credentials.get("api_base_url") or _RESEND_API_BASE)

    async def fetch_sender_profile(
        self, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        email = event.sender_email or event.reply_target.get("recipient_email")
        if not email:
            return None
        return SurfaceSenderProfile(
            external_user_id=str(email),
            email=str(email),
            display_name=event.sender_display_name,
        )

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._send_email(
            recipient_email=str(event.reply_target.get("recipient_email") or ""),
            subject=event.reply_target.get("subject"),
            in_reply_to=str(event.reply_target.get("in_reply_to") or "").strip() or None,
            references=[str(r) for r in (event.reply_target.get("references") or [])],
            content=message,
            content_type="markdown",
            attachments=[],
            display_resource_plans=coerce_display_resource_plans(
                (metadata or {}).get("display_resource_plans")
            ),
        )

    async def send_display_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._send_email(
            recipient_email=str(event.reply_target.get("recipient_email") or ""),
            subject=event.reply_target.get("subject"),
            in_reply_to=str(event.reply_target.get("in_reply_to") or "").strip() or None,
            references=[str(r) for r in (event.reply_target.get("references") or [])],
            content="",
            content_type="markdown",
            attachments=[],
            display_resource_plans=[render_plan],
        )

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # Email has no typing indicator.
        return None

    async def reply_email(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: ResendReplyEmailParams,
    ) -> ResendReplyEmailResult:
        metadata = ctx.deps.surface_metadata
        if not isinstance(metadata, ResendSurfaceEventMetadata):
            return ResendReplyEmailResult(
                success=False,
                error="Email reply tools are only available in email surface conversations.",
            )
        if not metadata.reply_to_email:
            return ResendReplyEmailResult(
                success=False,
                error="The current email is missing a reply recipient address.",
            )

        attachments, attachment_links = await resolve_outbound_email_attachments(
            ctx.deps,
            request.attachment_paths,
            inline_cap_bytes=attachment_cap("RESEND"),
        )
        content = append_attachment_links(request.content, attachment_links)

        try:
            response = await self._send_email(
                recipient_email=metadata.reply_to_email,
                subject=request.subject or metadata.subject or "",
                in_reply_to=metadata.in_reply_to,
                references=list(metadata.references),
                content=content,
                content_type=request.content_type,
                attachments=attachments,
            )
        except Exception as exc:
            return ResendReplyEmailResult(success=False, error=f"Email reply failed: {exc}")

        return ResendReplyEmailResult(
            success=True,
            message="Sent the email reply on the current thread.",
            thread_id=metadata.thread_id,
            message_id=str((response or {}).get("id") or "").strip() or None,
            attachment_count=len(attachments),
        )

    async def _send_email(
        self,
        *,
        recipient_email: str,
        subject: str | None,
        in_reply_to: str | None,
        references: list[str],
        content: str,
        content_type: str,
        attachments: list[tuple[str, bytes, str]],
        display_resource_plans: list[SurfaceDisplayRenderPlan] | None = None,
    ) -> dict[str, Any]:
        if not recipient_email or not self._api_key or not self._from_address:
            raise ValueError("Resend send requires api_key, from_address and a recipient.")

        plain_text, html_body = render_email_content(
            content=content,
            content_type=content_type,  # type: ignore[arg-type]
            display_resource_plans=display_resource_plans,
        )
        sender = (
            f"{self._from_name} <{self._from_address}>"
            if self._from_name
            else self._from_address
        )
        payload: dict[str, Any] = {
            "from": sender,
            "to": [recipient_email],
            "subject": reply_subject(subject),
            "text": plain_text,
        }
        if html_body:
            payload["html"] = html_body
        headers: dict[str, str] = {}
        if in_reply_to:
            headers["In-Reply-To"] = in_reply_to
        if references:
            headers["References"] = " ".join(references)
        if headers:
            payload["headers"] = headers
        if attachments:
            payload["attachments"] = [
                {
                    "filename": name,
                    "content": base64.b64encode(file_bytes).decode("ascii"),
                }
                for name, file_bytes, _mime in attachments
            ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._api_base.rstrip('/')}/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import httpx

from app.modules.agent_surfaces.platforms.common import assert_safe_api_base

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.email_render import (
    coerce_display_resource_plans,
    render_email_content,
)
from app.modules.agent_surfaces.platforms.email_text import reply_subject
from app.modules.agent_surfaces.platforms.composio_email import (
    execute_composio_operation,
    is_composio_credentials,
)

_GMAIL_API_BASE = "https://gmail.googleapis.com"
_GMAIL_APP_ID = "gmail"


class GmailPlatformService:
    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials
        self._is_composio = is_composio_credentials(credentials)
        self._access_token = credentials.get("access_token") or ""
        self._api_base = credentials.get("api_base_url") or _GMAIL_API_BASE

    async def fetch_sender_profile(
        self, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return SurfaceSenderProfile(
            external_user_id=event.sender_external_user_id,
            email=event.sender_email,
            display_name=event.sender_display_name,
        )

    @staticmethod
    def _reply_coordinates(
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Where this reply goes and what thread it joins.

        One reader, used by every send on this surface. There used to be two --
        this one off ``event.reply_target`` and the reply tool's off the
        conversation's stored metadata -- and when they disagreed the reply
        threaded somewhere else, so the recipient's answer arrived as a brand
        new conversation with no history and nothing logged.
        """
        target = event.reply_target
        return {
            "recipient_email": str(target.get("recipient_email") or "").strip(),
            "subject": str(
                target.get("subject") or (metadata or {}).get("subject") or ""
            ).strip(),
            "thread_id": str(target.get("thread_id") or "").strip() or None,
            "in_reply_to": str(target.get("in_reply_to") or "").strip() or None,
            "references": [str(ref) for ref in list(target.get("references") or []) if ref],
        }

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._send_email(
            **self._reply_coordinates(event, metadata),
            content=message,
            content_type=str((metadata or {}).get("content_type") or "markdown"),
            display_resource_plans=coerce_display_resource_plans(
                (metadata or {}).get("display_resource_plans")
            ),
            attachments=list((metadata or {}).get("attachments") or []),
        )

    async def _render_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._send_email(
            recipient_email=str(
                event.reply_target.get("recipient_email") or ""
            ).strip(),
            subject=str(
                event.reply_target.get("subject")
                or (metadata or {}).get("subject")
                or render_plan.title
            ).strip(),
            thread_id=str(event.reply_target.get("thread_id") or "").strip() or None,
            in_reply_to=(
                str(event.reply_target.get("in_reply_to") or "").strip() or None
            ),
            references=[
                str(ref)
                for ref in list(event.reply_target.get("references") or [])
                if ref
            ],
            content="",
            content_type="html",
            display_resource_plans=[render_plan],
            attachments=[],
        )

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def _send_email(
        self,
        *,
        recipient_email: str,
        subject: str,
        thread_id: str | None,
        in_reply_to: str | None,
        references: list[str],
        content: str,
        content_type: str,
        attachments: list[tuple[str, bytes, str]],
        attachment_url: str | None = None,
        display_resource_plans: list[SurfaceDisplayRenderPlan] | None = None,
    ) -> dict[str, Any]:
        plain_text, html_body = render_email_content(
            content=content,
            content_type=content_type,
            display_resource_plans=display_resource_plans,
        )

        if self._is_composio:
            # GMAIL_REPLY_TO_THREAD keeps the reply on-thread. Composio downloads
            # a URL passed in `attachment` and attaches it (single file); the
            # remaining files were folded into the body as links by the caller.
            if not thread_id:
                raise ValueError(
                    "Gmail reply through Composio requires the source thread id."
                )
            payload: dict[str, Any] = {
                "thread_id": thread_id,
                "message_body": html_body or plain_text,
                "is_html": bool(html_body),
            }
            if recipient_email:
                payload["recipient_email"] = recipient_email
            if attachment_url:
                payload["attachment"] = attachment_url
            data = await execute_composio_operation(
                connector_id=_GMAIL_APP_ID,
                operation_name="GMAIL_REPLY_TO_THREAD",
                payload=payload,
                credentials=self.credentials,
            )
            return data if isinstance(data, dict) else {}

        email_message = EmailMessage()
        email_message["To"] = recipient_email
        email_message["Subject"] = reply_subject(subject)
        if in_reply_to:
            email_message["In-Reply-To"] = in_reply_to
        if references:
            email_message["References"] = " ".join(references)

        email_message.set_content(plain_text or "")
        if html_body:
            email_message.add_alternative(html_body, subtype="html")
        for file_name, file_bytes, mime_type in attachments:
            maintype, subtype = mime_type.split("/", 1)
            email_message.add_attachment(
                file_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=file_name,
            )

        raw = base64.urlsafe_b64encode(email_message.as_bytes()).decode("ascii")
        payload: dict[str, Any] = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id

        url = f"{self._api_base.rstrip('/')}/gmail/v1/users/me/messages/send"
        await assert_safe_api_base(self._api_base, platform="Gmail")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            return response.json()

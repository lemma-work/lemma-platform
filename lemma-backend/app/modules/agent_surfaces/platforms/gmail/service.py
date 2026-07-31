from __future__ import annotations

import base64
import mimetypes
from email.message import EmailMessage
from typing import Any

import httpx
from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    GmailSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.attachment_limits import inline_cap
from app.modules.agent_surfaces.platforms.email_common import (
    append_attachment_links,
    coerce_display_resource_plans,
    decode_base64_bytes,
    render_email_content,
    reply_subject,
    resolve_outbound_email_attachments,
    resolve_outbound_email_attachment_urls,
)
from app.modules.agent_surfaces.platforms.composio_email import (
    execute_composio_operation,
    fetch_composio_file_bytes,
    is_composio_credentials,
)
from app.modules.agent_surfaces.platforms.email_models import (
    GmailFileAttachment,
    GmailReplyEmailParams,
    GmailReplyEmailResult,
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

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._send_email(
            recipient_email=str(
                event.reply_target.get("recipient_email") or ""
            ).strip(),
            subject=str(
                event.reply_target.get("subject")
                or (metadata or {}).get("subject")
                or ""
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
            content=message,
            content_type=str((metadata or {}).get("content_type") or "text"),
            display_resource_plans=coerce_display_resource_plans(
                (metadata or {}).get("display_resource_plans")
            ),
            attachments=[],
        )

    async def send_display_resource(
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

    async def reply_email(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: GmailReplyEmailParams,
    ) -> GmailReplyEmailResult:
        metadata = self._gmail_metadata(ctx)
        if metadata is None:
            return GmailReplyEmailResult(
                success=False,
                error="Gmail reply tools are only available in Gmail surface conversations.",
            )
        if not metadata.reply_to_email:
            return GmailReplyEmailResult(
                success=False,
                error="The current Gmail message is missing a reply recipient email.",
            )

        # Composio's Gmail action attaches a file passed as a URL in its
        # `attachment` field (single file), so datastore paths become signed URLs:
        # the first is attached natively, the rest are appended as links.
        if self._is_composio:
            url_attachments, unresolved = await resolve_outbound_email_attachment_urls(
                ctx.deps, request.attachment_paths
            )
            primary_url = url_attachments[0][1] if url_attachments else None
            content = append_attachment_links(request.content, url_attachments[1:])
            if unresolved:
                content = (
                    f"{content}\n\nCould not attach: {', '.join(unresolved)}"
                    if content
                    else f"Could not attach: {', '.join(unresolved)}"
                )
            try:
                response = await self._send_email(
                    recipient_email=metadata.reply_to_email,
                    subject=request.subject or metadata.subject or "",
                    thread_id=metadata.thread_id,
                    in_reply_to=metadata.in_reply_to,
                    references=list(metadata.references),
                    content=content,
                    content_type=request.content_type,
                    attachments=[],
                    attachment_url=primary_url,
                )
            except Exception as exc:
                return GmailReplyEmailResult(
                    success=False,
                    error=f"Gmail reply failed: {exc}",
                )
            return GmailReplyEmailResult(
                success=True,
                message="Sent Gmail reply on the current email thread.",
                thread_id=metadata.thread_id,
                message_id=str((response or {}).get("id") or "").strip() or None,
                attachment_count=1 if primary_url else 0,
            )

        # Native (non-Composio) path: attachment paths resolve against the pod
        # datastore (/me/...) or the workspace; files at/below the inline cap are
        # attached inline, larger ones become download links.
        attachments, attachment_links = await resolve_outbound_email_attachments(
            ctx.deps,
            request.attachment_paths,
            inline_cap_bytes=inline_cap("GMAIL"),
        )
        content = append_attachment_links(request.content, attachment_links)

        try:
            response = await self._send_email(
                recipient_email=metadata.reply_to_email,
                subject=request.subject or metadata.subject or "",
                thread_id=metadata.thread_id,
                in_reply_to=metadata.in_reply_to,
                references=list(metadata.references),
                content=content,
                content_type=request.content_type,
                attachments=attachments,
            )
        except Exception as exc:
            return GmailReplyEmailResult(
                success=False,
                error=f"Gmail reply failed: {exc}",
            )

        return GmailReplyEmailResult(
            success=True,
            message="Sent Gmail reply on the current email thread.",
            thread_id=metadata.thread_id,
            message_id=str((response or {}).get("id") or "").strip() or None,
            attachment_count=len(attachments),
        )

    def _gmail_metadata(
        self,
        ctx: RunContext[ConversationContext],
    ) -> GmailSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, GmailSurfaceEventMetadata):
            return metadata
        return None

    async def _resolve_attachment_bytes(
        self,
        attachment: GmailFileAttachment,
    ) -> bytes:
        if attachment.content_bytes_base64:
            return decode_base64_bytes(attachment.content_bytes_base64, urlsafe=True)
        if not attachment.id or not attachment.message_id:
            raise ValueError(
                "The Gmail attachment payload did not include inline data or a retrievable attachment id."
            )

        if self._is_composio:
            data = await execute_composio_operation(
                connector_id=_GMAIL_APP_ID,
                operation_name="GMAIL_GET_ATTACHMENT",
                payload={
                    "message_id": attachment.message_id,
                    "attachment_id": attachment.id,
                    "file_name": attachment.name or "gmail_attachment",
                },
                credentials=self.credentials,
            )
            return await fetch_composio_file_bytes(data)

        url = (
            f"{self._api_base.rstrip('/')}/gmail/v1/users/me/messages/"
            f"{attachment.message_id}/attachments/{attachment.id}"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            payload = response.json()

        data = str((payload or {}).get("data") or "").strip()
        if not data:
            raise ValueError(
                "Gmail attachment response did not contain attachment data."
            )
        return decode_base64_bytes(data, urlsafe=True)

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Gmail attachment (no RunContext)."""
        del event
        try:
            att = GmailFileAttachment.model_validate(attachment)
        except Exception:
            return None
        if not att.content_bytes_base64 and not (att.id and att.message_id):
            return None
        content = await self._resolve_attachment_bytes(att)
        file_name = (att.name or "").strip() or "gmail_attachment"
        mime_type = (
            (att.mime_type or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

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
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            return response.json()

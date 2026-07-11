from __future__ import annotations

import base64
import mimetypes
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
    OutlookSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.attachment_limits import inline_cap
from app.modules.agent_surfaces.platforms.email_common import (
    append_attachment_links,
    coerce_display_resource_plans,
    render_email_content,
    reply_subject,
    resolve_outbound_email_attachments,
    resolve_outbound_email_attachment_urls,
)
from app.modules.agent_surfaces.platforms.email_models import (
    OutlookFileAttachment,
    OutlookReplyEmailParams,
    OutlookReplyEmailResult,
)
from app.modules.agent_surfaces.platforms.composio_email import (
    execute_composio_operation,
    fetch_composio_file_bytes,
    is_composio_credentials,
)
from app.modules.agent_surfaces.platforms.outlook.parser import OutlookMessageParser

_GRAPH_API_BASE = "https://graph.microsoft.com"
_OUTLOOK_APP_ID = "outlook"


class OutlookPlatformService:
    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials
        self._is_composio = is_composio_credentials(credentials)
        self._access_token = credentials.get("access_token") or ""
        self._api_base = credentials.get("api_base_url") or _GRAPH_API_BASE

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
        provider_message_id = str(
            event.reply_target.get("message_id")
            or event.metadata.get("message_id")
            or event.external_message_id
            or ""
        ).strip()
        if not provider_message_id:
            raise ValueError(
                "Outlook reply could not determine the provider message id."
            )
        await self._reply_to_message(
            message_id=provider_message_id,
            content=message,
            content_type=str((metadata or {}).get("content_type") or "text"),
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
        del metadata
        provider_message_id = str(
            event.reply_target.get("message_id")
            or event.metadata.get("message_id")
            or event.external_message_id
            or ""
        ).strip()
        if not provider_message_id:
            raise ValueError(
                "Outlook display resource reply could not determine the provider message id."
            )
        await self._reply_to_message(
            message_id=provider_message_id,
            content="",
            content_type="html",
            display_resource_plans=[render_plan],
        )

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def enrich_event(
        self,
        event: ParsedInboundSurfaceEvent,
    ) -> ParsedInboundSurfaceEvent | None:
        if not event.metadata.get("requires_message_fetch"):
            return event

        provider_message_id = str(
            event.reply_target.get("message_id")
            or event.metadata.get("message_id")
            or event.external_message_id
            or ""
        ).strip()
        if not provider_message_id:
            return None

        message = await self._fetch_message(provider_message_id)
        enriched = OutlookMessageParser().parse(message)
        if enriched is None:
            return None

        enriched.raw_payload = {
            "trigger_payload": event.raw_payload,
            "message_payload": message,
        }
        return enriched

    async def reply_email(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: OutlookReplyEmailParams,
    ) -> OutlookReplyEmailResult:
        metadata = self._outlook_metadata(ctx)
        if metadata is None:
            return OutlookReplyEmailResult(
                success=False,
                error="Outlook reply tools are only available in Outlook surface conversations.",
            )
        if not metadata.reply_to_email:
            return OutlookReplyEmailResult(
                success=False,
                error="The current Outlook message is missing a reply recipient email.",
            )

        effective_message_id = str(metadata.message_id or "").strip()
        if not effective_message_id:
            return OutlookReplyEmailResult(
                success=False,
                error="The current Outlook message is missing a provider message id.",
            )

        # Composio's Outlook action attaches a file passed as a URL in its
        # `attachment` field (single file), so datastore paths become signed URLs:
        # the first is attached natively, the rest are appended as links.
        if self._is_composio:
            url_attachments, unresolved = await resolve_outbound_email_attachment_urls(
                ctx.deps, request.attachment_paths
            )
            primary_url = url_attachments[0][1] if url_attachments else None
            composio_content = append_attachment_links(
                request.content, url_attachments[1:]
            )
            if unresolved:
                composio_content = (
                    f"{composio_content}\n\nCould not attach: {', '.join(unresolved)}"
                    if composio_content
                    else f"Could not attach: {', '.join(unresolved)}"
                )
            try:
                await self._reply_to_message(
                    message_id=effective_message_id,
                    content=composio_content,
                    content_type=request.content_type,
                    attachment_url=primary_url,
                )
            except Exception as exc:
                return OutlookReplyEmailResult(
                    success=False,
                    error=f"Outlook reply failed: {exc}",
                )
            return OutlookReplyEmailResult(
                success=True,
                message="Sent Outlook reply on the current email thread.",
                thread_id=metadata.thread_id,
                message_id=None,
                attachment_count=1 if primary_url else 0,
            )

        # Native (Graph) path: files within the inline cap are attached via the
        # draft flow; larger files become download links appended to the body.
        inline_files, attachment_links = await resolve_outbound_email_attachments(
            ctx.deps,
            request.attachment_paths,
            inline_cap_bytes=inline_cap("OUTLOOK"),
        )
        content = append_attachment_links(request.content, attachment_links)
        attachments: list[dict[str, Any]] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": mime,
                "contentBytes": base64.b64encode(file_bytes).decode("ascii"),
            }
            for name, file_bytes, mime in inline_files
        ]

        try:
            if attachments:
                draft_id = await self._create_reply_draft(
                    message_id=effective_message_id
                )
                await self._update_draft(
                    message_id=draft_id,
                    content=content,
                    content_type=request.content_type,
                    subject=request.subject or metadata.subject or "",
                )
                for attachment in attachments:
                    await self._add_attachment_to_draft(
                        message_id=draft_id,
                        attachment=attachment,
                    )
                await self._send_draft(message_id=draft_id)
            else:
                await self._reply_to_message(
                    message_id=effective_message_id,
                    content=content,
                    content_type=request.content_type,
                )
        except Exception as exc:
            return OutlookReplyEmailResult(
                success=False,
                error=f"Outlook reply failed: {exc}",
            )

        return OutlookReplyEmailResult(
            success=True,
            message="Sent Outlook reply on the current email thread.",
            thread_id=metadata.thread_id,
            message_id=None,
            attachment_count=len(attachments),
        )

    def _outlook_metadata(
        self,
        ctx: RunContext[ConversationContext],
    ) -> OutlookSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, OutlookSurfaceEventMetadata):
            return metadata
        return None

    async def _download_attachment_bytes(
        self,
        *,
        message_id: str,
        attachment_id: str,
        file_name: str = "outlook_attachment",
    ) -> bytes:
        if self._is_composio:
            data = await execute_composio_operation(
                connector_id=_OUTLOOK_APP_ID,
                operation_name="OUTLOOK_DOWNLOAD_OUTLOOK_ATTACHMENT",
                payload={
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "file_name": file_name,
                },
                credentials=self.credentials,
            )
            return await fetch_composio_file_bytes(data)

        url = (
            f"{self._api_base.rstrip('/')}/v1.0/me/messages/"
            f"{message_id}/attachments/{attachment_id}"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            payload = response.json()

        content_bytes = str((payload or {}).get("contentBytes") or "").strip()
        if not content_bytes:
            raise ValueError(
                "Outlook attachment response did not include contentBytes. "
                "Linked or non-file attachments are not supported by this tool."
            )
        return base64.b64decode(content_bytes.encode("ascii"))

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Outlook attachment (no RunContext)."""
        del event
        try:
            att = OutlookFileAttachment.model_validate(attachment)
        except Exception:
            return None
        file_name = (att.name or "").strip() or "outlook_attachment"
        if att.content_bytes_base64:
            content = base64.b64decode(att.content_bytes_base64.encode("ascii"))
        elif att.id and att.message_id:
            content = await self._download_attachment_bytes(
                message_id=att.message_id,
                attachment_id=att.id,
                file_name=file_name,
            )
        else:
            return None
        mime_type = (
            (att.mime_type or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

    async def _fetch_message(self, message_id: str) -> dict[str, Any]:
        if self._is_composio:
            # Don't pass `select`: Composio rejects several fields the parser
            # needs (conversationId, internetMessageId) as select values, yet
            # the default response already includes them plus body/from.
            data = await execute_composio_operation(
                connector_id=_OUTLOOK_APP_ID,
                operation_name="OUTLOOK_GET_MESSAGE",
                payload={"message_id": message_id},
                credentials=self.credentials,
            )
            return data if isinstance(data, dict) else {}

        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}"
        params = {
            "$expand": "attachments",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)
            return response.json()

    async def _reply_to_message(
        self,
        *,
        message_id: str,
        content: str,
        content_type: str,
        attachment_url: str | None = None,
        display_resource_plans: list[SurfaceDisplayRenderPlan] | None = None,
    ) -> None:
        plain_text, html_body = render_email_content(
            content=content,
            content_type=content_type,
            display_resource_plans=display_resource_plans,
        )

        if self._is_composio:
            # Composio downloads a URL passed in `attachment` and attaches it
            # (single file); the remaining files are folded into the body as links.
            payload: dict[str, Any] = {
                "message_id": message_id,
                "comment": html_body or plain_text,
                "is_html": bool(html_body),
            }
            if attachment_url:
                payload["attachment"] = attachment_url
            await execute_composio_operation(
                connector_id=_OUTLOOK_APP_ID,
                operation_name="OUTLOOK_REPLY_EMAIL",
                payload=payload,
                credentials=self.credentials,
            )
            return

        body_content_type = "HTML" if html_body else "Text"
        body_content = html_body if html_body else plain_text

        payload = {
            "message": {
                "body": {
                    "contentType": body_content_type,
                    "content": body_content,
                }
            },
        }

        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/reply"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    async def _create_reply_draft(self, *, message_id: str) -> str:
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/createReply"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)
            payload = response.json()
        draft_id = str((payload or {}).get("id") or "").strip()
        if not draft_id:
            raise ValueError("Outlook createReply response did not include a draft id.")
        return draft_id

    async def _update_draft(
        self,
        *,
        message_id: str,
        content: str,
        content_type: str,
        subject: str,
        display_resource_plans: list[SurfaceDisplayRenderPlan] | None = None,
    ) -> None:
        plain_text, html_body = render_email_content(
            content=content,
            content_type=content_type,
            display_resource_plans=display_resource_plans,
        )
        body_content_type = "HTML" if html_body else "Text"
        body_content = html_body if html_body else plain_text
        payload = {
            "subject": reply_subject(subject),
            "body": {
                "contentType": body_content_type,
                "content": body_content,
            },
        }
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.patch(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    async def _add_attachment_to_draft(
        self,
        *,
        message_id: str,
        attachment: dict[str, Any],
    ) -> None:
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/attachments"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=attachment,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    async def _send_draft(self, *, message_id: str) -> None:
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/send"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            response_text = response.text.strip()
            if response_text:
                raise httpx.HTTPStatusError(
                    f"{exc}. Response body: {response_text}",
                    request=exc.request,
                    response=exc.response,
                ) from exc
            raise

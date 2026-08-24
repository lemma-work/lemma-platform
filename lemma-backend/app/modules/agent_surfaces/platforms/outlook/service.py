from __future__ import annotations

import base64
import json
import mimetypes
from typing import Any

import httpx

from app.modules.agent_surfaces.platforms.common import assert_safe_api_base
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
from app.modules.agent_surfaces.platforms.attachment_limits import email_inline_cap
from app.modules.agent_surfaces.platforms.email_attachments import (
    append_attachment_links,
    resolve_outbound_email_attachment_urls,
    resolve_outbound_email_attachments,
)
from app.modules.agent_surfaces.platforms.email_render import (
    coerce_display_resource_plans,
    render_email_content,
)
from app.modules.agent_surfaces.platforms.email_text import reply_subject
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
from app.core.concurrency.offload import run_blocking

_GRAPH_API_BASE = "https://graph.microsoft.com"
_OUTLOOK_APP_ID = "outlook"


def _decode_graph_attachment(raw: bytes) -> bytes:
    """Parse a Graph attachment response and decode its payload, off the loop."""
    payload = json.loads(raw)
    content_bytes = str((payload or {}).get("contentBytes") or "").strip()
    if not content_bytes:
        raise ValueError(
            "Outlook attachment response did not include contentBytes. "
            "Linked or non-file attachments are not supported by this tool."
        )
    return base64.b64decode(content_bytes.encode("ascii"))


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
            content_type=str((metadata or {}).get("content_type") or "markdown"),
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
        message_id = str(metadata.message_id or "").strip()
        if not message_id:
            return OutlookReplyEmailResult(
                success=False,
                error="The current Outlook message is missing a provider message id.",
            )

        content, attachments, attachment_url = await self._resolve_reply_attachments(
            ctx, request
        )
        try:
            await self._deliver_reply(
                message_id=message_id,
                content=content,
                content_type=request.content_type,
                subject=request.subject or metadata.subject or "",
                attachments=attachments,
                attachment_url=attachment_url,
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
            attachment_count=(1 if attachment_url else 0) + len(attachments),
        )

    async def _resolve_reply_attachments(
        self,
        ctx: RunContext[ConversationContext],
        request: OutlookReplyEmailParams,
    ) -> tuple[str, list[dict[str, Any]], str | None]:
        """The body and attachments to send, by how this account is connected.

        Composio's Outlook action attaches a single file passed as a URL, so
        datastore paths become signed URLs: the first is attached natively and
        the rest are appended as links. The Graph path can carry bytes, so files
        within the inline cap ride along and larger ones become download links.
        """
        if not self._is_composio:
            inline_files, links = await resolve_outbound_email_attachments(
                ctx.deps,
                request.attachment_paths,
                inline_cap_bytes=email_inline_cap("OUTLOOK"),
            )
            return (
                append_attachment_links(request.content, links),
                [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": name,
                        "contentType": mime,
                        "contentBytes": base64.b64encode(file_bytes).decode("ascii"),
                    }
                    for name, file_bytes, mime in inline_files
                ],
                None,
            )

        url_attachments, unresolved = await resolve_outbound_email_attachment_urls(
            ctx.deps, request.attachment_paths
        )
        content = append_attachment_links(request.content, url_attachments[1:])
        if unresolved:
            note = f"Could not attach: {', '.join(unresolved)}"
            content = f"{content}\n\n{note}" if content else note
        return content, [], (url_attachments[0][1] if url_attachments else None)

    async def _deliver_reply(
        self,
        *,
        message_id: str,
        content: str,
        content_type: str,
        subject: str,
        attachments: list[dict[str, Any]],
        attachment_url: str | None,
    ) -> None:
        """Send the reply, through a draft when it has files to carry.

        Graph will not attach bytes to a direct reply, so a message with inline
        attachments has to become a draft first, be filled in, and then be sent.
        """
        if not attachments:
            await self._reply_to_message(
                message_id=message_id,
                content=content,
                content_type=content_type,
                attachment_url=attachment_url,
            )
            return

        draft_id = await self._create_reply_draft(message_id=message_id)
        await self._update_draft(
            message_id=draft_id,
            content=content,
            content_type=content_type,
            subject=subject,
        )
        for attachment in attachments:
            await self._add_attachment_to_draft(
                message_id=draft_id,
                attachment=attachment,
            )
        await self._send_draft(message_id=draft_id)

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
        # Tenant-supplied base (sovereign-cloud endpoints are real), so the
        # target is checked before the token is sent.
        await assert_safe_api_base(self._api_base, platform="Outlook")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            raw = response.content

        # Graph returns the file base64-encoded INSIDE the JSON body, so a 50 MB
        # attachment arrives as ~67 MB of JSON. Parsing that and then decoding
        # the base64 is CPU proportional to the attachment, and both halves ran
        # on the event loop.
        return await run_blocking(_decode_graph_attachment, raw, limiter="cpu_bound")

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
        await assert_safe_api_base(self._api_base, platform="Outlook")
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
        await assert_safe_api_base(self._api_base, platform="Outlook")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    async def _create_reply_draft(self, *, message_id: str) -> str:
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/createReply"
        await assert_safe_api_base(self._api_base, platform="Outlook")
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
        await assert_safe_api_base(self._api_base, platform="Outlook")
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
        await assert_safe_api_base(self._api_base, platform="Outlook")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json=attachment,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._raise_for_status(response)

    async def _send_draft(self, *, message_id: str) -> None:
        url = f"{self._api_base.rstrip('/')}/v1.0/me/messages/{message_id}/send"
        await assert_safe_api_base(self._api_base, platform="Outlook")
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

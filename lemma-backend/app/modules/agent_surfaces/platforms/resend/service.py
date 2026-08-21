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
from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
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
            in_reply_to=str(event.reply_target.get("in_reply_to") or "").strip()
            or None,
            references=[str(r) for r in (event.reply_target.get("references") or [])],
            content=message,
            content_type="markdown",
            attachments=[],
            display_resource_plans=coerce_display_resource_plans(
                (metadata or {}).get("display_resource_plans")
            ),
        )

    def _raise_unsendable(self, recipient_email: str) -> None:
        """Say which part is missing, and raise something delivery can catch.

        ``AgentSurfaceError``, not ``ValueError``: notification delivery catches
        the former per channel, while a bare ``ValueError`` escapes ``notify()``
        and rolls back the notification row that was deliberately written before
        the send — losing the very record that design exists to keep. Naming the
        missing field matters too: this message has been read three times as
        "Resend is unconfigured" when the key was present and only the
        surface-derived sender was absent.
        """
        missing = [
            name
            for name, value in (
                ("api_key", self._api_key),
                ("from_address", self._from_address),
                ("recipient", recipient_email),
            )
            if not value
        ]
        raise AgentSurfaceValidationError(
            f"Resend send is missing: {', '.join(missing)}."
        )

    async def fetch_received_email(self, email_id: str) -> dict[str, Any]:
        """Retrieve a received email's body, headers and attachment metadata.

        Resend's ``email.received`` webhook is metadata only — it carries no
        body and no headers beyond ``message_id`` — so this call is not an
        enrichment nicety, it is the only way to learn what the person wrote.
        """
        if not self._api_key:
            raise AgentSurfaceValidationError("Resend receive requires an api_key.")
        if not email_id:
            raise AgentSurfaceValidationError("Resend receive requires an email id.")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._api_base.rstrip('/')}/emails/receiving/{email_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            return response.json() if response.content else {}

    async def list_received_emails(
        self, *, after: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """List recently received emails, newest first, for polling ingestion.

        The counterpart to the inbound webhook for runtimes without one (the
        desktop app): the poller walks this list to discover new email ids, then
        ``fetch_received_email`` fills each body in. ``after`` is a Resend cursor
        (an email id) for pagination. Returns ``{"data": [...], "has_more": ...}``.
        """
        if not self._api_key:
            raise AgentSurfaceValidationError("Resend receive requires an api_key.")
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        if after:
            params["after"] = after
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._api_base.rstrip('/')}/emails/receiving",
                headers={"Authorization": f"Bearer {self._api_key}"},
                params=params,
            )
            response.raise_for_status()
            return response.json() if response.content else {}

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Fetch one inbound attachment, so the agent gets a real file.

        Two hops, because Resend does not serve the bytes from its API: the
        attachment endpoint returns metadata with a short-lived signed
        ``download_url``, and the content comes from there. ``email_id`` is the
        same handle the body fetch uses, carried on the event's metadata.
        """
        email_id = str((event.metadata or {}).get("email_id") or "").strip()
        attachment_id = str(attachment.get("id") or "").strip()
        if not (self._api_key and email_id and attachment_id):
            return None

        async with httpx.AsyncClient(timeout=30.0) as client:
            described = await client.get(
                f"{self._api_base.rstrip('/')}/emails/receiving/"
                f"{email_id}/attachments/{attachment_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            described.raise_for_status()
            payload = described.json() if described.content else {}
            url = str(payload.get("download_url") or "").strip()
            if not url:
                return None
            # The signed URL is not the Resend API and must not carry the key.
            content = await client.get(url)
            content.raise_for_status()
            name = str(
                payload.get("filename") or attachment.get("name") or "attachment"
            )
            mime = str(
                payload.get("content_type")
                or attachment.get("content_type")
                or "application/octet-stream"
            )
            return content.content, name, mime

    async def send_cold_email(
        self,
        *,
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ColdEmailSendResult:
        """First contact: no thread to reply into, so address the mailbox.

        The seed goes in ``References`` and ``In-Reply-To`` stays empty. A reply's
        ``References`` is the original's plus the original's ``Message-ID``, so
        the seed lands first and the inbound parser — which reads
        ``references[0]`` as the thread root — recognises it. Resend generates
        the ``Message-ID`` itself and its response ``id`` is a Resend object id,
        not an RFC one, so seeding the chain is the only handle we get.
        """
        response = await self._send_email(
            recipient_email=recipient_email,
            subject=subject,
            in_reply_to=None,
            references=[thread_seed_id],
            content=message,
            content_type="markdown",
            attachments=[],
            display_resource_plans=coerce_display_resource_plans(
                (metadata or {}).get("display_resource_plans")
            ),
            is_reply=False,
        )
        return ColdEmailSendResult(
            external_thread_id=thread_seed_id,
            external_message_id=str((response or {}).get("id") or "").strip() or None,
            reply_target={
                "recipient_email": recipient_email,
                "subject": subject,
                # Keeps a follow-up we send before they reply on the same thread.
                "references": [thread_seed_id],
            },
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
            in_reply_to=str(event.reply_target.get("in_reply_to") or "").strip()
            or None,
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
            return ResendReplyEmailResult(
                success=False, error=f"Email reply failed: {exc}"
            )

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
        is_reply: bool = True,
    ) -> dict[str, Any]:
        if not recipient_email or not self._api_key or not self._from_address:
            self._raise_unsendable(recipient_email)

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
            # ``reply_subject`` prefixes "Re:", which is right for a reply and
            # wrong for first contact — a notification nobody has ever seen
            # arriving as "Re: Standup" reads as a message you have lost.
            "subject": reply_subject(subject) if is_reply else (subject or "").strip(),
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

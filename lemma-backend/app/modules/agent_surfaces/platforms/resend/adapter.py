"""Resend email surface adapter."""

from __future__ import annotations

from typing import Any

from httpx import HTTPError

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.email_one_reply import (
    EmailOneReplyMixin,
)
from app.modules.agent_surfaces.platforms.common import provider_failure
from app.modules.agent_surfaces.platforms.resend.parser import (
    ResendInboundParser,
    merge_received_email,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService

logger = get_logger(__name__)


class ResendSurfaceAdapter(EmailOneReplyMixin, BaseSurfaceAdapter):
    platform = "RESEND"

    def __init__(self) -> None:
        self._parser = ResendInboundParser()

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self._parser.parse(payload, headers)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return await ResendPlatformService(credentials).fetch_sender_profile(event)

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Let inbound email attachments become pod files like every other surface.

        Without this the base class returned None, so a file somebody emailed to
        an agent was named in the metadata and never fetched — the agent was told
        an attachment existed and had no way to open it.
        """
        return await ResendPlatformService(credentials).download_attachment_bytes(
            event, attachment
        )

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent | None:
        """Fetch the body when the webhook did not carry one.

        Returning ``None`` drops the event, and for an email we cannot read that
        is the right failure: the alternative is starting an agent run whose
        user message is the empty string, which is what shipped and what made
        every inbound email look like the agent ignoring people.

        The fetch is **not** unconditional, and that is the point. When the
        webhook already carries the body, asking the API for it again puts a
        working message behind a call that can fail — and it did: a Resend key
        restricted to sending answers ``GET /emails/receiving`` with 401, so a
        reply we had already been handed in full was dropped and retried eight
        times while the person who sent it heard nothing.

        So: keep what arrived, use the fetch to fill what is missing, and only
        fail when there is nothing to work with.

        The fetch also re-derives the thread root from ``References``. When the
        webhook carries headers that is already done by the parser; when it does
        not, this is the only way a reply rejoins its conversation instead of
        opening a new one.
        """
        service = ResendPlatformService(credentials)
        email_id = str((event.metadata or {}).get("email_id") or "").strip()
        has_body = bool(event.message_text.strip())

        if not email_id:
            logger.warning(
                "agent_surfaces.resend.inbound_missing_email_id.degraded",
                thread_id=event.external_thread_id,
            )
            return event if has_body else None

        try:
            received = await service.fetch_received_email(email_id)
        except (HTTPError, OSError) as exc:
            if has_body:
                # We can already read it. Losing a message we hold because a
                # secondary call failed is strictly worse than proceeding on
                # what the provider pushed us.
                failure = provider_failure(exc)
                logger.warning(
                    "agent_surfaces.resend.inbound_fetch_skipped.degraded",
                    thread_id=event.external_thread_id,
                    failure_type=failure.failure_type,
                    status_code=failure.status_code,
                    provider_error=failure.provider_error,
                )
                return event
            raise

        return merge_received_email(event, received)

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials).send_message(event, message, metadata)

    async def send_cold_email(
        self,
        *,
        credentials: dict[str, Any],
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ColdEmailSendResult | None:
        return await ResendPlatformService(credentials).send_cold_email(
            recipient_email=recipient_email,
            subject=subject,
            message=message,
            thread_seed_id=thread_seed_id,
            metadata=metadata,
        )

    async def _render_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials)._render_resource(
            event, render_plan, metadata
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials).add_processing_indicator(
            event, metadata
        )


__all__ = ["ResendSurfaceAdapter", "ResendInboundParser", "ResendPlatformService"]

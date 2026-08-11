"""Resend email surface adapter."""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    ColdEmailSendResult,
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.resend.parser import (
    ResendInboundParser,
    merge_received_email,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService

logger = get_logger(__name__)


class ResendSurfaceAdapter(BaseSurfaceAdapter):
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

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent | None:
        """Fetch the body the webhook did not carry.

        Returning ``None`` drops the event, and that is the right failure: the
        alternative is starting an agent run whose user message is the empty
        string, which is what shipped and what made every inbound email look
        like the agent ignoring people.

        Also re-derives the thread root, because ``References`` only exists once
        the headers arrive — without this every reply opens a new conversation
        no matter what we seeded the outbound with.
        """
        email_id = str((event.metadata or {}).get("email_id") or "").strip()
        if not email_id:
            logger.warning(
                "agent_surfaces.resend.inbound_missing_email_id.degraded",
                thread_id=event.external_thread_id,
            )
            return None if not event.message_text.strip() else event

        received = await ResendPlatformService(credentials).fetch_received_email(
            email_id
        )
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

    async def send_display_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials).send_display_resource(
            event, render_plan, metadata
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials).add_processing_indicator(event, metadata)


__all__ = ["ResendSurfaceAdapter", "ResendInboundParser", "ResendPlatformService"]

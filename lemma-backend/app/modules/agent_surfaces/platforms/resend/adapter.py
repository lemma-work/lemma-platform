"""Resend email surface adapter."""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.resend.parser import ResendInboundParser
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService


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

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await ResendPlatformService(credentials).send_message(event, message, metadata)

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

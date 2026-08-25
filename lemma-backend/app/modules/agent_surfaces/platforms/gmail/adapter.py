from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.gmail.parser import GmailMessageParser
from app.modules.agent_surfaces.platforms.gmail.service import (
    GmailPlatformService,
)


class ComposioGmailSurfaceAdapter(BaseSurfaceAdapter):
    platform = "GMAIL"

    def __init__(self) -> None:
        self._parser = GmailMessageParser()

    async def parse_inbound_event(
        self, payload: dict[str, object], headers: dict[str, str] | None = None
    ):
        return self._parser.parse(payload)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, object], event
    ) -> SurfaceSenderProfile | None:
        return await GmailPlatformService(credentials).fetch_sender_profile(event)

    async def send_message(
        self,
        *,
        credentials: dict[str, object],
        event,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await GmailPlatformService(credentials).send_message(event, message, metadata)

    async def _render_resource(
        self,
        *,
        credentials: dict[str, object],
        event,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await GmailPlatformService(credentials)._render_resource(
            event,
            render_plan,
            metadata,
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, object],
        event,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await GmailPlatformService(credentials).add_processing_indicator(
            event, metadata
        )

    async def download_attachment(
        self,
        *,
        credentials: dict[str, object],
        event,
        attachment: dict[str, object],
    ) -> tuple[bytes, str, str] | None:
        return await GmailPlatformService(credentials).download_attachment_bytes(
            event, attachment
        )


GmailSurfaceAdapter = ComposioGmailSurfaceAdapter

__all__ = [
    "ComposioGmailSurfaceAdapter",
    "GmailMessageParser",
    "GmailPlatformService",
    "GmailSurfaceAdapter",
]

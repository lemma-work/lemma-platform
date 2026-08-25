from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.outlook.parser import OutlookMessageParser
from app.modules.agent_surfaces.platforms.outlook.service import (
    OutlookPlatformService,
)


class ComposioOutlookSurfaceAdapter(BaseSurfaceAdapter):
    platform = "OUTLOOK"

    def __init__(self) -> None:
        self._parser = OutlookMessageParser()

    async def parse_inbound_event(
        self, payload: dict[str, object], headers: dict[str, str] | None = None
    ):
        return self._parser.parse(payload)

    async def enrich_inbound_event(self, *, credentials: dict[str, object], event):
        return await OutlookPlatformService(credentials).enrich_event(event)

    async def fetch_sender_profile(
        self, *, credentials: dict[str, object], event
    ) -> SurfaceSenderProfile | None:
        return await OutlookPlatformService(credentials).fetch_sender_profile(event)

    async def send_message(
        self,
        *,
        credentials: dict[str, object],
        event,
        message: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await OutlookPlatformService(credentials).send_message(event, message, metadata)

    async def _render_resource(
        self,
        *,
        credentials: dict[str, object],
        event,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, object] | None = None,
    ) -> None:
        await OutlookPlatformService(credentials)._render_resource(
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
        await OutlookPlatformService(credentials).add_processing_indicator(
            event, metadata
        )

    async def download_attachment(
        self,
        *,
        credentials: dict[str, object],
        event,
        attachment: dict[str, object],
    ) -> tuple[bytes, str, str] | None:
        return await OutlookPlatformService(credentials).download_attachment_bytes(
            event, attachment
        )


OutlookSurfaceAdapter = ComposioOutlookSurfaceAdapter

__all__ = [
    "ComposioOutlookSurfaceAdapter",
    "OutlookMessageParser",
    "OutlookPlatformService",
    "OutlookSurfaceAdapter",
]

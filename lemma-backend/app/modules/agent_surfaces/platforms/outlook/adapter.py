from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.email_one_reply import (
    EmailOneReplyMixin,
)
from app.modules.agent_surfaces.platforms.outlook.parser import OutlookMessageParser
from app.modules.agent_surfaces.platforms.outlook.service import (
    OutlookPlatformService,
)


class ComposioOutlookSurfaceAdapter(EmailOneReplyMixin, BaseSurfaceAdapter):
    platform = "OUTLOOK"

    def __init__(self) -> None:
        self._parser = OutlookMessageParser()

    async def _attachment_payload(self, credentials, envelope):
        """Composio downloads a signed URL; Graph carries bytes.

        Restored from the reply tool this replaced, and the reason
        ``EnvelopeFile`` carries both a ``source_path`` and a ``signed_url``: a
        Composio account cannot use the bytes at all. Signing needs pod services
        an adapter cannot reach, so the caller that built the envelope did it;
        here we only choose which of the two to use. Composio's action takes
        exactly one file, so the first is attached and the rest become links.
        """
        from app.modules.agent_surfaces.platforms.composio_email import (
            is_composio_credentials,
        )

        if not is_composio_credentials(credentials):
            # Graph wants its own attachment shape, not the (name, bytes, mime)
            # every other provider takes.
            import base64

            return (
                [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": item.file_name,
                        "contentType": item.mime_type,
                        "contentBytes": base64.b64encode(item.content).decode("ascii"),
                    }
                    for item in envelope.files
                ],
                {},
            )

        linked = [item for item in envelope.files if item.signed_url]
        missing = [item.file_name for item in envelope.files if not item.signed_url]
        extra: dict = {}
        if linked:
            extra["attachment_url"] = linked[0].signed_url
        notes = [f"{item.file_name}: {item.signed_url}" for item in linked[1:]]
        if missing:
            notes.append(f"Could not attach: {', '.join(missing)}")
        if notes:
            extra["body_note"] = "\n".join(notes)
        return [], extra

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

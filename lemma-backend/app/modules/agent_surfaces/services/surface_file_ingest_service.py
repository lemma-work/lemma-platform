"""Auto-ingest user-provided surface attachments into the pod datastore.

When a user sends a file on any surface (Slack/Telegram/WhatsApp/Teams/email),
inbound ingress downloads it and persists it to the pod datastore under
``/me/{platform}`` — the same store and ``/me/...`` convention as web uploads —
so surface files and web files behave identically. The agent is told about the
saved path via a NOTIFICATION message; to send a file back it uses the
``display_resource`` tool (type=FILE), and the egress layer decides whether to
attach the bytes or send a link.

The download itself is delegated to the platform adapter's ``download_attachment``
(not an agent tool). Failures are isolated per file: a download/write error logs
and is skipped — never blocking the agent run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
    INBOUND_VOICE_TRANSCRIBE_BYTE_CAP,
)
from app.composition.surface_datastore import build_file_service
from app.modules.datastore.contracts import normalize_datastore_name

logger = get_logger(__name__)

_AUDIO_CONTENT_TYPES = {"voice", "audio"}


@dataclass(slots=True)
class IngestedAttachment:
    """A persisted inbound attachment + the metadata ingress needs downstream.

    ``audio_bytes`` is carried only for audio attachments small enough to
    transcribe (so the transcription step doesn't re-download); it is ``None``
    for non-audio or oversize audio.
    """

    path: str
    name: str
    mime: str | None = None
    content_type: str | None = None
    audio_bytes: bytes | None = None

    @property
    def is_audio(self) -> bool:
        return (self.mime or "").lower().startswith("audio/") or (
            (self.content_type or "").lower() in _AUDIO_CONTENT_TYPES
        )


def _attachments_from_parsed(parsed: ParsedInboundSurfaceEvent) -> list[dict[str, Any]]:
    raw = (parsed.metadata or {}).get("attachments")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _safe_file_name(name: str | None) -> str:
    """Reduce an attachment name to a single valid datastore segment."""
    candidate = Path(str(name or "").strip().replace("\\", "/")).name.strip()
    candidate = candidate.replace("/", "_").strip() or "attachment"
    try:
        return normalize_datastore_name(candidate)
    except Exception:
        return "attachment"


class SurfaceFileIngestService:
    """Download inbound surface attachments and persist them to the datastore."""

    def __init__(
        self,
        *,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
    ) -> None:
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()

    async def ingest_attachments(
        self,
        *,
        pod_id: UUID,
        platform: str,
        user_id: UUID,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
    ) -> list[IngestedAttachment]:
        """Persist each inbound attachment to ``/me/{platform}``; return results.

        Runs in its own unit of work so file persistence is independent of the
        conversation transaction. Returns one :class:`IngestedAttachment` per
        saved file (best effort — failed files are skipped, not raised). Audio
        files small enough to transcribe also carry their bytes so the caller can
        transcribe without a re-download.
        """
        attachments = _attachments_from_parsed(parsed)
        if not attachments:
            return []
        platform_key = platform.value if hasattr(platform, "value") else str(platform)
        adapter = self.adapter_registry.get(platform_key)
        if adapter is None:
            return []

        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            auth_ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id,
                pod_id=pod_id,
            )
            token = set_current_context(auth_ctx)
            try:
                file_service = build_file_service(uow)
                saved = await self._ingest_all(
                    adapter=adapter,
                    pod_id=pod_id,
                    platform=platform_key,
                    parsed=parsed,
                    credentials=credentials,
                    file_service=file_service,
                    ctx=auth_ctx,
                    attachments=attachments,
                )
                if saved:
                    await uow.commit()
            finally:
                reset_current_context(token)
        return saved

    async def _ingest_all(
        self,
        *,
        adapter: Any,
        pod_id: UUID,
        platform: str,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
        file_service: Any,
        ctx: Any,
        attachments: list[dict[str, Any]],
    ) -> list[IngestedAttachment]:
        """Core ingest loop — pure of DB/session setup so it is unit-testable
        with a fake adapter and file service."""
        directory = f"/me/{str(platform).lower()}"
        saved: list[IngestedAttachment] = []
        for attachment in attachments:
            result = await self._ingest_one(
                adapter=adapter,
                pod_id=pod_id,
                platform=platform,
                parsed=parsed,
                credentials=credentials,
                file_service=file_service,
                ctx=ctx,
                directory=directory,
                attachment=attachment,
            )
            if result is not None:
                saved.append(result)
        return saved

    async def _ingest_one(
        self,
        *,
        adapter: Any,
        pod_id: UUID,
        platform: str,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
        file_service: Any,
        ctx: Any,
        directory: str,
        attachment: dict[str, Any],
    ) -> IngestedAttachment | None:
        declared_size = attachment.get("size")
        if (
            isinstance(declared_size, int)
            and declared_size > INBOUND_ATTACHMENT_BYTE_CAP
        ):
            logger.info(
                "Surface attachment skipped (too large) platform=%s size=%s",
                platform,
                declared_size,
            )
            return None

        try:
            downloaded = await adapter.download_attachment(
                credentials=credentials,
                event=parsed,
                attachment=attachment,
            )
        except Exception as exc:
            logger.warning(
                "Surface attachment download failed platform=%s error=%s",
                platform,
                exc,
            )
            return None
        if downloaded is None:
            return None

        content, name, mime = downloaded
        if len(content) > INBOUND_ATTACHMENT_BYTE_CAP:
            logger.info(
                "Surface attachment skipped (too large after download) platform=%s "
                "size=%s",
                platform,
                len(content),
            )
            return None

        try:
            entity = await file_service.create_file(
                pod_id=pod_id,
                name=_safe_file_name(name),
                file_content=content,
                ctx=ctx,
                directory_path=directory,
                search_enabled=True,
            )
        except Exception as exc:
            logger.warning(
                "Surface attachment persist failed platform=%s name=%s error=%s",
                platform,
                name,
                exc,
            )
            return None

        content_type = attachment.get("content_type")
        result = IngestedAttachment(
            path=entity.path,
            name=entity.name,
            mime=mime,
            content_type=str(content_type) if content_type else None,
        )
        # Carry audio bytes for in-ingress transcription when small enough; larger
        # audio is still saved (the agent can `listen` to it) but not transcribed.
        if result.is_audio and len(content) <= INBOUND_VOICE_TRANSCRIBE_BYTE_CAP:
            result.audio_bytes = content
        return result

"""Auto-ingest user-provided surface attachments into the pod datastore.

When a user sends a file on any surface (Slack/Telegram/WhatsApp/Teams/email),
inbound ingress downloads it and persists it to the pod datastore under
``/me/{platform}`` — the same store and ``/me/...`` convention as web uploads —
so surface files and web files behave identically. The agent is told about the
saved path via a NOTIFICATION message; to send a file back it uses the
``display_resource`` tool (type=FILE), and the egress layer decides whether to
attach the bytes or send a link.

The download itself is delegated to the platform adapter's ``download_attachment``
(not an agent tool). Failures are isolated per file: a download/write error never
blocks the agent run. It is still *reported* — one bad file comes back as an
``AttachmentFailure`` rather than as nothing, because an agent told nothing
answers as though the person had attached nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
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
from app.core.net.capped_read import ResponseTooLargeError
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
    INBOUND_VOICE_TRANSCRIBE_BYTE_CAP,
)
from app.modules.datastore.contracts.surfaces import create_pod_file
from app.core.file_types import extension_for_mime, sniff_media_mime
from app.modules.datastore.contracts import (
    DatastoreConflictError,
    normalize_datastore_name,
)

logger = get_logger(__name__)

_AUDIO_CONTENT_TYPES = {"voice", "audio"}

# Writes one file into a pod and answers with what it stored. Production binds
# datastore's `create_pod_file` to a transaction; a unit test hands over a
# stand-in that records the call.
PodFileWriter = Callable[..., Awaitable[Any]]

# Given a callable that wants a writer, run it in whatever transaction is
# appropriate and return what it produced. Production opens a fresh one per
# file; unit tests hand a fake writer straight through.
StoreInTransaction = Callable[
    [Callable[[PodFileWriter], Awaitable["IngestedAttachment | None"]]],
    Awaitable["IngestedAttachment | None"],
]


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


@dataclass(slots=True)
class AttachmentFailure:
    """An attachment the platform announced that never reached the datastore.

    Kept rather than dropped because the agent is otherwise told nothing at all:
    a photo whose download failed is indistinguishable, from inside the run,
    from a message that had no photo on it -- so the agent answers as though the
    person sent only text, and the person is left watching it ignore their file.
    ``reason`` is written to be read by the agent, so it says what the person
    can do about it rather than which call raised.
    """

    name: str
    reason: str


@dataclass(slots=True)
class AttachmentIngest:
    """What became of every attachment on one inbound message."""

    saved: list[IngestedAttachment] = field(default_factory=list)
    failed: list[AttachmentFailure] = field(default_factory=list)


def _attachments_from_parsed(parsed: ParsedInboundSurfaceEvent) -> list[dict[str, Any]]:
    raw = (parsed.metadata or {}).get("attachments")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def every_attachment_failed(
    parsed: ParsedInboundSurfaceEvent, *, reason: str
) -> AttachmentIngest:
    """Report each announced attachment as one that never arrived.

    For the failures that are not per-file: no adapter to download with, or the
    ingest call coming apart as a whole. The caller cannot enumerate what was
    lost -- the names live in the parsed event -- and an empty
    :class:`AttachmentIngest` would tell the agent the message had no files on
    it at all.
    """
    return AttachmentIngest(
        failed=[
            AttachmentFailure(name=_attachment_label(item), reason=reason)
            for item in _attachments_from_parsed(parsed)
        ]
    )


def _safe_file_name(
    name: str | None, mime: str | None = None, content: bytes | None = None
) -> str:
    """Reduce an attachment name to a single valid datastore segment.

    The extension is part of the name's job here. A chat surface is free to send
    a file with no filename -- WhatsApp names a photo nothing at all, Telegram
    calls every one of them ``photo`` -- and the datastore types a file by its
    name alone. Saved bare, the photo comes back as ``application/octet-stream``:
    ``view_image`` refuses it, indexing skips it, and the agent is left holding a
    path to something it cannot open. So when the name carries no suffix and the
    download told us what the bytes are, say so in the name.

    ``content`` is the last resort, for the surface that declares nothing at all:
    a magic number is a fact about the file where a missing header is only an
    absence. Read only when the name and the declared type have both failed, so
    the ordinary case costs nothing.
    """
    candidate = Path(str(name or "").strip().replace("\\", "/")).name.strip()
    candidate = candidate.replace("/", "_").strip() or "attachment"
    if not Path(candidate).suffix:
        extension = extension_for_mime(mime)
        if not extension and content:
            extension = extension_for_mime(sniff_media_mime(content))
        candidate += extension or ""
    try:
        return normalize_datastore_name(candidate)
    except Exception:
        return "attachment"


# How many names to try before giving up on one attachment. A person sending a
# handful of photos in a row is ordinary; a hundred identically-named ones is
# not worth a hundred round trips.
_NAME_ATTEMPTS = 25


def _attachment_label(attachment: dict[str, Any]) -> str:
    """What to call an attachment when telling someone it did not make it.

    The parsed name, which on a chat surface is often only the type word
    ("image", "photo") — that is still what the person sees in their own
    transcript, so it is the name that identifies the file to them.
    """
    return str(attachment.get("name") or "").strip() or "the file"


def _numbered(name: str, attempt: int) -> str:
    """``image.jpg`` -> ``image-2.jpg`` for the second one, and so on.

    Chat surfaces name nothing: every WhatsApp photo is ``image``, every
    Telegram one is ``photo``. The datastore refuses a duplicate path, so
    without this the *second* photo anyone ever sent was dropped -- logged as a
    warning nobody was reading, with the agent told only about the first.
    """
    if attempt == 0:
        return name
    stem, dot, extension = name.rpartition(".")
    if not dot:
        return f"{name}-{attempt + 1}"
    return f"{stem}-{attempt + 1}.{extension}"


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
    ) -> AttachmentIngest:
        """Persist each inbound attachment to ``/me/{platform}``; return results.

        Runs in its own unit of work so file persistence is independent of the
        conversation transaction. Never raises for one bad file (best effort),
        but a skipped file is now *reported* rather than dropped — see
        :class:`AttachmentFailure`. Audio files small enough to transcribe also
        carry their bytes so the caller can transcribe without a re-download.
        """
        attachments = _attachments_from_parsed(parsed)
        if not attachments:
            return AttachmentIngest()
        platform_key = platform.value if hasattr(platform, "value") else str(platform)
        adapter = self.adapter_registry.get(platform_key)
        if adapter is None:
            # Nothing can be downloaded without one, and the person still
            # attached something — so this is every attachment failing, not
            # nothing to do.
            return every_attachment_failed(
                parsed, reason="this surface cannot receive files"
            )

        # Three phases, and the middle one is the reason for the shape: an
        # attachment is up to 50 MB over a 60s-timeout HTTP call, once per file.
        # Building the context and storing the bytes are the only parts that
        # need a connection, so those are the only parts that have one.
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            auth_ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id,
                pod_id=pod_id,
            )
        token = set_current_context(auth_ctx)
        try:
            outcome = await self._ingest_all(
                adapter=adapter,
                pod_id=pod_id,
                platform=platform_key,
                parsed=parsed,
                credentials=credentials,
                ctx=auth_ctx,
                attachments=attachments,
                store=self._store_in_own_transaction,
            )
        finally:
            reset_current_context(token)
        return outcome

    @staticmethod
    async def _store_in_own_transaction(
        persist_ingested_attachment: Callable[
            [PodFileWriter], Awaitable[IngestedAttachment | None]
        ],
    ) -> IngestedAttachment | None:
        """Run one file's write in a transaction of its own.

        One per attachment rather than one for the batch, because the batch
        version could not commit until the last download finished -- so the
        first file's row locks were held across every remaining transfer. There
        is no atomicity lost: the previous code committed between attachments
        too (that was how it let the connection go), so a partial batch was
        always a possible outcome.
        """
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            result = await persist_ingested_attachment(partial(create_pod_file, uow))
            if result is not None:
                await uow.commit()
            return result

    async def _ingest_all(
        self,
        *,
        adapter: Any,
        pod_id: UUID,
        platform: str,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
        ctx: Any,
        attachments: list[dict[str, Any]],
        store: StoreInTransaction,
    ) -> AttachmentIngest:
        """Core ingest loop — pure of DB/session setup so it is unit-testable
        with a fake adapter and file service.

        ``store`` is what keeps it that way while still holding no pooled
        connection across a download: it is handed a callable that wants a file
        service and returns the saved attachment, and decides for itself what
        transaction to run it in. Production opens one per file; a unit test
        passes a fake service straight through. The loop knows the write needs
        a boundary; it does not need to know what the boundary is.
        """
        directory = f"/me/{str(platform).lower()}"
        outcome = AttachmentIngest()
        for attachment in attachments:
            result = await self._ingest_one(
                adapter=adapter,
                pod_id=pod_id,
                platform=platform,
                parsed=parsed,
                credentials=credentials,
                ctx=ctx,
                directory=directory,
                attachment=attachment,
                store=store,
            )
            if isinstance(result, IngestedAttachment):
                outcome.saved.append(result)
            else:
                outcome.failed.append(result)
        return outcome

    async def _ingest_one(
        self,
        *,
        adapter: Any,
        pod_id: UUID,
        platform: str,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
        ctx: Any,
        directory: str,
        attachment: dict[str, Any],
        store: StoreInTransaction,
    ) -> IngestedAttachment | AttachmentFailure:
        label = _attachment_label(attachment)
        declared_size = attachment.get("size")
        if (
            isinstance(declared_size, int)
            and declared_size > INBOUND_ATTACHMENT_BYTE_CAP
        ):
            return AttachmentFailure(
                name=label,
                reason=(
                    "it is larger than the "
                    f"{INBOUND_ATTACHMENT_BYTE_CAP // (1024 * 1024)} MB limit"
                ),
            )

        # The download runs with no session open at all. It used to run inside
        # one that committed first to hand the connection back -- correct, but
        # only legible as a comment, and invisible to the static gate because
        # the release arrived as a callback.
        try:
            downloaded = await adapter.download_attachment(
                credentials=credentials,
                event=parsed,
                attachment=attachment,
            )
        except ResponseTooLargeError:
            # Its own case because it is not a failure: the adapter abandoned
            # the transfer mid-stream, on purpose, rather than letting the
            # sender pick how much of this replica's heap to fill.
            logger.info(
                "agent_surfaces.file_ingest.attachment_over_cap",
                platform=platform,
                cap_bytes=INBOUND_ATTACHMENT_BYTE_CAP,
            )
            return AttachmentFailure(
                name=label,
                reason=(
                    "it is larger than the "
                    f"{INBOUND_ATTACHMENT_BYTE_CAP // (1024 * 1024)} MB limit"
                ),
            )
        except Exception:
            # Skipping one attachment should not sink the whole message, so the
            # broad catch stays -- but it used to swallow the reason entirely
            # and return None, which made "the file never arrived" unanswerable
            # from the logs. Now the traceback is there.
            logger.warning(
                "agent_surfaces.file_ingest.attachment_download_failed.degraded",
                platform=platform,
                exc_info=True,
            )
            return AttachmentFailure(name=label, reason="the download failed")
        if downloaded is None:
            return AttachmentFailure(name=label, reason="the download failed")

        content, name, mime = downloaded
        if len(content) > INBOUND_ATTACHMENT_BYTE_CAP:
            # Belt and braces. Adapters stream under the cap now, so reaching
            # this means one of them read a whole body some other way -- keep
            # the check, and say so rather than dropping the file in silence.
            logger.warning(
                "agent_surfaces.file_ingest.attachment_over_cap_after_read.degraded",
                platform=platform,
                size_bytes=len(content),
                cap_bytes=INBOUND_ATTACHMENT_BYTE_CAP,
            )
            return AttachmentFailure(
                name=label,
                reason=(
                    "it is larger than the "
                    f"{INBOUND_ATTACHMENT_BYTE_CAP // (1024 * 1024)} MB limit"
                ),
            )

        content_type = attachment.get("content_type")
        file_name = _safe_file_name(name, mime, content)

        def _persist_as(candidate: str):
            async def _persist(write_file: PodFileWriter) -> IngestedAttachment | None:
                entity = await write_file(
                    pod_id=pod_id,
                    name=candidate,
                    content=content,
                    ctx=ctx,
                    directory_path=directory,
                    search_enabled=True,
                )
                result = IngestedAttachment(
                    path=entity.path,
                    name=entity.name,
                    mime=mime,
                    content_type=str(content_type) if content_type else None,
                )
                # Carry audio bytes for in-ingress transcription when small
                # enough; larger audio is still saved (the agent can `listen` to
                # it) but not transcribed.
                if (
                    result.is_audio
                    and len(content) <= INBOUND_VOICE_TRANSCRIBE_BYTE_CAP
                ):
                    result.audio_bytes = content
                return result

            return _persist

        try:
            for attempt in range(_NAME_ATTEMPTS):
                try:
                    stored = await store(_persist_as(_numbered(file_name, attempt)))
                except DatastoreConflictError:
                    # That name is taken -- by an earlier photo from this same
                    # person, almost always. Try the next one.
                    continue
                if stored is not None:
                    return stored
                break
            logger.warning(
                "agent_surfaces.file_ingest.attachment_name_unavailable.degraded",
                platform=platform,
                attempts=_NAME_ATTEMPTS,
            )
            return AttachmentFailure(name=label, reason="it could not be saved")
        except Exception:
            logger.warning(
                "agent_surfaces.file_ingest.attachment_store_failed.degraded",
                platform=platform,
                exc_info=True,
            )
            return AttachmentFailure(name=label, reason="it could not be saved")

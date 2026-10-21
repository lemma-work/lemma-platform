"""Persist standardized ACP images as conversation-scoped pod files.

An image arrives one of two ways. A current Agent Host uploads its bytes
first and the event names them, ``{"type": "image", "mimeType", "lemma_blob":
{"sha256", "size"}}`` (``domain/agent_host_transfer``), so no event frame
carries an image across the 1 MiB link. An older host still sends ``data``
inline, base64; that is read as before, for as long as its frame fits.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.modules.datastore.contracts.agent_tools import build_file_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host_transfer import BLOB_FIELD, is_sha256_hex
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.agent_host.transfers import (
    AgentHostTransferStore,
)
from app.modules.datastore.contracts import DatastoreConflictError

logger = get_logger(__name__)

MAX_ACP_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
}


@dataclass(frozen=True, slots=True)
class SavedAgentHostArtifact:
    path: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class AgentHostArtifactMaterialization:
    markdown: str = ""
    warnings: tuple[str, ...] = ()


class AgentHostArtifactWriter(Protocol):
    async def materialize_event(
        self,
        *,
        payload: JsonObject,
        pod_id: UUID,
        user_context: AgentContext,
        directory_path: str,
        agent_run_id: UUID,
        event_sequence: int,
        harness_key: str,
    ) -> AgentHostArtifactMaterialization: ...


@dataclass(frozen=True, slots=True)
class _InlineImage:
    """One image block: its base64 ``data``, or the digest of an uploaded blob."""

    data: str | None
    mime_type: str
    blob_sha256: str | None = None


class BlobReader(Protocol):
    """Where an uploaded image is read back from (``AgentHostTransferStore``)."""

    async def get_blob(self, *, run_id: UUID, sha256: str) -> bytes | None: ...


class PodFileAgentHostArtifactWriter:
    """Decode ACP image/resource blocks and store them in the pod filesystem."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        blobs: BlobReader | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self._blobs: BlobReader = blobs or AgentHostTransferStore()

    async def materialize_event(
        self,
        *,
        payload: JsonObject,
        pod_id: UUID,
        user_context: AgentContext,
        directory_path: str,
        agent_run_id: UUID,
        event_sequence: int,
        harness_key: str,
    ) -> AgentHostArtifactMaterialization:
        saved: list[SavedAgentHostArtifact] = []
        warnings: list[str] = []
        for index, image in enumerate(_inline_images(payload)):
            try:
                content = await self._image_bytes(image, agent_run_id=agent_run_id)
                extension = _IMAGE_EXTENSIONS[image.mime_type]
                name = (
                    f"agent-image-{agent_run_id}-{event_sequence}-{index + 1}."
                    f"{extension}"
                )
                path = f"{directory_path.rstrip('/')}/{name}"
                try:
                    async with self.uow_factory() as uow:
                        await build_file_service(uow).create_file(
                            pod_id,
                            name,
                            content,
                            user_context,
                            description=(
                                f"Image returned by the {harness_key} harness over ACP"
                            ),
                            metadata={
                                "source": "agent_host",
                                "protocol": "ACP",
                                "agent_run_id": str(agent_run_id),
                                "event_sequence": event_sequence,
                                "mime_type": image.mime_type,
                            },
                            directory_path=directory_path,
                            search_enabled=False,
                        )
                except DatastoreConflictError:
                    # A replay targets the same deterministic path. The first
                    # successful write wins, making event handling idempotent.
                    pass
                saved.append(
                    SavedAgentHostArtifact(
                        path=path,
                        mime_type=image.mime_type,
                        size_bytes=len(content),
                    )
                )
            except ValueError as exc:
                warnings.append(str(exc))
            except (DomainError, OSError, SQLAlchemyError, TimeoutError) as exc:
                raise_if_database_unavailable(exc)
                logger.error(
                    "agent_host.artifact.persist_failed",
                    agent_run_id=str(agent_run_id),
                    event_sequence=event_sequence,
                    harness_key=harness_key,
                    exc_info=True,
                )
                warnings.append("An ACP image could not be saved to pod files.")

        return AgentHostArtifactMaterialization(
            markdown=_artifact_markdown(saved),
            warnings=tuple(warnings),
        )

    async def _image_bytes(self, image: _InlineImage, *, agent_run_id: UUID) -> bytes:
        if image.blob_sha256 is None:
            return _decode_image(image)
        if image.mime_type not in _IMAGE_EXTENSIONS:
            raise ValueError(f"ACP returned unsupported image type {image.mime_type}.")
        try:
            content = await self._blobs.get_blob(
                run_id=agent_run_id, sha256=image.blob_sha256
            )
        except RedisError:
            logger.warning(
                "agent.agent_host.artifact_blob_unavailable.degraded",
                agent_run_id=str(agent_run_id),
                exc_info=True,
            )
            raise ValueError(
                "An image the agent produced could not be fetched to save."
            ) from None
        if content is None:
            raise ValueError(
                "An image the agent produced was not uploaded, or expired before "
                "it could be saved."
            )
        return _checked_image(content, image.mime_type)


def _inline_images(payload: JsonObject) -> list[_InlineImage]:
    """Extract ACP-owned content positions without crawling arbitrary tool JSON."""
    blocks: list[object] = []
    _extend_content_blocks(blocks, payload.get("content"))
    result = payload.get("result")
    if isinstance(result, dict):
        _extend_content_blocks(blocks, result.get("content"))

    images: list[_InlineImage] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "image":
            holder: object = block
            data = block.get("data")
        elif block_type == "resource":
            holder = block.get("resource")
            if not isinstance(holder, dict):
                continue
            data = holder.get("blob")
        else:
            continue
        mime_type = holder.get("mimeType") if isinstance(holder, dict) else None
        if not isinstance(mime_type, str):
            continue
        normalized_mime = mime_type.split(";", 1)[0].strip().lower()
        if not normalized_mime.startswith("image/"):
            continue
        blob = _blob_digest(holder)
        if blob is not None:
            images.append(
                _InlineImage(data=None, mime_type=normalized_mime, blob_sha256=blob)
            )
        elif isinstance(data, str):
            images.append(_InlineImage(data=data, mime_type=normalized_mime))
    return images


def _blob_digest(holder: object) -> str | None:
    """The SHA-256 a block names its uploaded bytes by, if it names any."""
    if not isinstance(holder, dict):
        return None
    reference = holder.get(BLOB_FIELD)
    if not isinstance(reference, dict):
        return None
    digest = reference.get("sha256")
    return digest if is_sha256_hex(digest) else None


def _extend_content_blocks(target: list[object], content: object) -> None:
    if isinstance(content, list):
        target.extend(content)
    elif isinstance(content, dict):
        target.append(content)


def _decode_image(image: _InlineImage) -> bytes:
    if image.mime_type not in _IMAGE_EXTENSIONS:
        raise ValueError(f"ACP returned unsupported image type {image.mime_type}.")
    data = image.data or ""
    if len(data) > ((MAX_ACP_INLINE_IMAGE_BYTES + 2) // 3) * 4 + 4:
        raise ValueError("ACP image exceeds the 5 MB decoded size limit.")
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("ACP returned invalid base64 image data.") from exc
    return _checked_image(content, image.mime_type)


def _checked_image(content: bytes, mime_type: str) -> bytes:
    if len(content) > MAX_ACP_INLINE_IMAGE_BYTES:
        raise ValueError("ACP image exceeds the 5 MB decoded size limit.")
    if not _matches_image_signature(content, mime_type):
        raise ValueError(f"ACP image bytes do not match declared type {mime_type}.")
    return content


def _matches_image_signature(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    if mime_type == "image/avif":
        return (
            len(content) >= 16
            and content[4:8] == b"ftyp"
            and (b"avif" in content[8:32] or b"avis" in content[8:32])
        )
    return False


def _artifact_markdown(artifacts: list[SavedAgentHostArtifact]) -> str:
    return "\n\n".join(
        f"![Generated image]({artifact.path})\n\n"
        f"Generated file: [{artifact.path}]({artifact.path})"
        for artifact in artifacts
    )

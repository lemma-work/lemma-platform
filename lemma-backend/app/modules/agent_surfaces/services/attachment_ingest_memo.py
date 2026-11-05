"""What one inbound message's attachments already became, for a retried job.

Ingesting an attachment and transcribing a voice note both happen before the
only database write of an inbound turn, and neither is idempotent: a file is
committed in its own transaction, and a transcription is a paid call. When the
write then fails and the job retries, every file was saved again under the next
free name (``image-2.jpg``) and every voice note transcribed again. The memo
remembers, per (pod, platform, channel, message, attachment index), the file a
retry should reuse and the transcript it already paid for.

Redis rather than a table: the memo only has to outlive the job's retries, and
a lost entry costs what the code did before -- one more copy -- rather than
anything being wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.exceptions import RedisError

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import INGESTED_ATTACHMENTS, surface_caches
from app.core.log.log import get_logger
from app.modules.agent.contracts.speech import VoiceTranscript
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent

logger = get_logger(__name__)

# Long enough to cover a job's retries, including a worker restart between them.
_MEMO_TTL_SECONDS = INGESTED_ATTACHMENTS.ttl_seconds
_REDIS_FAILURES = (RedisError, OSError, TimeoutError, ValueError, TypeError)


@dataclass(frozen=True, slots=True)
class RememberedAttachment:
    """The stored file one attachment became."""

    path: str
    name: str
    mime: str | None
    content_type: str | None


def memo_key(
    *,
    pod_id: UUID,
    platform: str,
    parsed: ParsedInboundSurfaceEvent,
    index: int,
) -> str | None:
    """The memo key for one attachment, or None when the message has no id.

    Without the platform's message id there is nothing a retry would key on
    the same way, so such a message is ingested as it always was.
    """
    if not parsed.external_message_id:
        return None
    return (
        f"{pod_id}:{platform.lower()}:{parsed.external_channel_id or '-'}:"
        f"{parsed.external_message_id}:{index}"
    )


class AttachmentIngestMemo:
    """Redis-backed recall of stored attachments and their transcripts."""

    def __init__(self, cache: RedisJsonCache | None = None) -> None:
        self._cache = cache

    def _get_cache(self) -> RedisJsonCache:
        return self._cache or surface_caches.get(INGESTED_ATTACHMENTS)

    async def recall(self, key: str | None) -> RememberedAttachment | None:
        entry = await self._read(f"file:{key}") if key else None
        if not isinstance(entry, dict) or not entry.get("path"):
            return None
        return RememberedAttachment(
            path=str(entry["path"]),
            name=str(entry.get("name") or ""),
            mime=entry.get("mime"),
            content_type=entry.get("content_type"),
        )

    async def remember(self, key: str | None, attachment: RememberedAttachment) -> None:
        if key:
            await self._write(
                f"file:{key}",
                {
                    "path": attachment.path,
                    "name": attachment.name,
                    "mime": attachment.mime,
                    "content_type": attachment.content_type,
                },
            )

    async def transcript(self, key: str | None) -> VoiceTranscript | None:
        entry = await self._read(f"transcript:{key}") if key else None
        if not isinstance(entry, dict) or not entry.get("text"):
            return None
        return VoiceTranscript(
            text=str(entry["text"]),
            detected_language=entry.get("detected_language"),
            duration_seconds=entry.get("duration_seconds"),
        )

    async def remember_transcript(
        self, key: str | None, transcript: VoiceTranscript
    ) -> None:
        if key and transcript.text.strip():
            await self._write(
                f"transcript:{key}",
                {
                    "text": transcript.text,
                    "detected_language": transcript.detected_language,
                    "duration_seconds": transcript.duration_seconds,
                },
            )

    async def _read(self, suffix: str) -> object | None:
        try:
            return await self._get_cache().get_json(suffix)
        except _REDIS_FAILURES:
            # A miss: the attachment is fetched and stored again, which is
            # what happened before the memo existed.
            logger.warning(
                "agent_surfaces.attachment_ingest_memo.read_failed.degraded",
                exc_info=True,
            )
            return None

    async def _write(self, suffix: str, payload: dict[str, object]) -> None:
        try:
            await self._get_cache().set_json(
                suffix, payload, ttl_seconds=_MEMO_TTL_SECONDS
            )
        except _REDIS_FAILURES:
            logger.warning(
                "agent_surfaces.attachment_ingest_memo.write_failed.degraded",
                exc_info=True,
            )

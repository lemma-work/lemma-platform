"""The row's side of document processing: claim it, finish it, give it back.

A file's `status` is the durable backlog -- the dispatcher offers PENDING rows
and nothing else -- so these transitions are the state machine that keeps the
pipeline honest about what is in flight, what failed, and what is owed a retry.
Kept together and away from the reads because they are the only methods here
that answer "what is happening to this file", and because `file_repository`
crossed the architecture ratchet's per-file ceiling.

A mixin rather than a separate repository so callers keep one object, the same
seam `file_recovery_queries` and `file_listing_reads` already draw.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.domain.file_entities import (
    FileStatus,
)
from app.modules.datastore.infrastructure.models import DatastoreFile


def _content_identity_matches(content_sha256: str | None):
    """Match the bytes a claim was taken against, NULL included.

    `== None` is not a comparison SQL makes, and a file with no digest is a real
    state -- so the claim it was taken under has to be matched with `IS NULL`
    rather than silently never matching.
    """
    if content_sha256 is None:
        return DatastoreFile.content_sha256.is_(None)
    return DatastoreFile.content_sha256 == content_sha256


class DatastoreFileProcessingStateMixin:
    """Processing-state transitions for the host repository's session."""

    session: AsyncSession

    async def mark_not_required(self, file_id: UUID) -> None:
        await self.session.execute(
            update(DatastoreFile)
            .where(DatastoreFile.id == file_id)
            .values(status=FileStatus.NOT_REQUIRED.value, indexed_at=None)
        )

    async def claim_for_processing(
        self, file_id: UUID, *, content_sha256: str | None
    ) -> int | None:
        """Atomically claim one content identity and return its attempt token."""
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PENDING.value,
                _content_identity_matches(content_sha256),
            )
            .values(
                status=FileStatus.PROCESSING.value,
                processing_attempts=DatastoreFile.processing_attempts + 1,
            )
            .returning(DatastoreFile.processing_attempts)
        )
        return result.scalar_one_or_none()

    async def is_processing_claim_current(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
    ) -> bool:
        return bool(
            await self.session.scalar(
                select(DatastoreFile.id).where(
                    DatastoreFile.id == file_id,
                    DatastoreFile.status == FileStatus.PROCESSING.value,
                    _content_identity_matches(content_sha256),
                    DatastoreFile.processing_attempts == processing_attempt,
                )
            )
        )

    async def mark_completed(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
        file_metadata: dict[str, object],
    ) -> bool:
        """Complete only the exact content identity and processing claim."""
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(
                status=FileStatus.COMPLETED.value,
                indexed_at=datetime.now(timezone.utc),
                last_processing_error=None,
                processing_attempts=0,
                file_metadata=file_metadata,
            )
        )
        return result.rowcount > 0

    async def mark_failed(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
        error: str,
    ) -> bool:
        """Fail only the exact content identity and processing claim."""
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(
                status=FileStatus.FAILED.value,
                last_processing_error=error,
            )
        )
        return result.rowcount > 0

    async def release_claim(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
    ) -> bool:
        """Return a claim to PENDING *without* spending an attempt.

        For infrastructure backpressure — the extractor is down, overloaded, or
        the circuit is open — the document itself is fine and nothing about it
        was learned. ``claim_for_processing`` incremented ``processing_attempts``
        on the way in, and the recovery cron terminally fails a file once that
        counter reaches ``datastore_recovery_max_attempts`` (3). Without this,
        three extractor blips are enough to mark a perfectly good user document
        FAILED_PERMANENT.

        So this decrements the counter back to its pre-claim value, which is what
        distinguishes "we could not reach the extractor" from "this document
        cannot be processed". Document-level failures keep using ``mark_failed``
        and do spend their attempt.

        Fenced on the same (status, content identity, attempt) triple as every
        other transition, so a stale worker cannot release a newer claim.
        """
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(
                status=FileStatus.PENDING.value,
                processing_attempts=DatastoreFile.processing_attempts - 1,
            )
        )
        return result.rowcount > 0

    async def mark_missing_original(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
        error: str,
    ) -> bool:
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(
                status=FileStatus.FAILED_PERMANENT.value,
                last_processing_error=error,
            )
        )
        return result.rowcount > 0

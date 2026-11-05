"""The row's side of document processing: claim it, finish it, give it back.

The ``file.process`` work item drives processing and owns its retries; the row
is what a person sees -- PENDING, PROCESSING, COMPLETED, FAILED_PERMANENT -- and
these are its transitions. Every one after the claim is fenced on the claim's
token (``processing_claim``, which only ever moves on) and the bytes it was
taken against, so a run that lost its lease cannot overwrite a newer run's
outcome. ``processing_attempts`` is the person's number, as before the work
runtime: attempts that failed on these bytes -- a claim counts one, an outage
gives it back, completing or asking again clears it. Kept together and away from the reads because
they are the only methods here that answer "what is happening to this file".

A mixin rather than a separate repository so callers keep one object, the same
seam `file_status_counts` and `file_listing_reads` already draw.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileStatus,
    ProcessingRequest,
)
from app.modules.datastore.events.file_work import (
    content_update_delay,
    enqueue_file_processing,
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

    async def _enqueue_requested_processing(
        self, entity: DatastoreFileEntity, instance: DatastoreFile
    ) -> None:
        """Write the work item a transition asked for, in this transaction."""
        request = entity.processing_request
        if request is None:
            return
        entity.clear_processing_request()
        # New bytes, a retry or search switched on: a fresh count. Only the
        # count -- the claim token is never reset, so it cannot be reissued.
        instance.processing_attempts = 0
        await enqueue_file_processing(
            self.session,
            pod_id=instance.pod_id,
            file_id=instance.id,
            sha256=instance.content_sha256,
            delay=(
                content_update_delay() if request is ProcessingRequest.CONTENT else None
            ),
        )

    async def mark_not_required(self, file_id: UUID) -> None:
        await self.session.execute(
            update(DatastoreFile)
            .where(DatastoreFile.id == file_id)
            .values(status=FileStatus.NOT_REQUIRED.value, indexed_at=None)
        )

    async def claim_for_processing(
        self, file_id: UUID, *, content_sha256: str | None
    ) -> int | None:
        """Claim one content identity and return the run's token.

        PROCESSING is claimable too: the work item, not the row, says whether a
        run is alive, and a run reaching here holds the item's lease -- so a
        PROCESSING row it finds was left by a run that died. The token moves
        on, which is what fences the dead run if it was only stalled.
        ``processing_attempts`` counts the claim too.
        """
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status.in_(
                    (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
                ),
                _content_identity_matches(content_sha256),
            )
            .values(
                status=FileStatus.PROCESSING.value,
                processing_attempts=DatastoreFile.processing_attempts + 1,
                processing_claim=DatastoreFile.processing_claim + 1,
            )
            .returning(DatastoreFile.processing_claim)
        )
        return result.scalar_one_or_none()

    async def is_processing_claim_current(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        claim: int,
    ) -> bool:
        return bool(
            await self.session.scalar(
                select(DatastoreFile.id).where(
                    DatastoreFile.id == file_id,
                    DatastoreFile.status == FileStatus.PROCESSING.value,
                    _content_identity_matches(content_sha256),
                    DatastoreFile.processing_claim == claim,
                )
            )
        )

    async def mark_completed(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        claim: int,
        file_metadata: dict[str, object],
    ) -> bool:
        """Complete only the exact content identity and processing claim."""
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_claim == claim,
            )
            .values(
                status=FileStatus.COMPLETED.value,
                indexed_at=datetime.now(timezone.utc),
                indexed_sha256=content_sha256,
                last_processing_error=None,
                file_metadata=file_metadata,
                processing_attempts=0,
            )
        )
        return result.rowcount > 0

    async def release_to_pending(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        claim: int,
        note: str | None,
        charged: bool,
    ) -> bool:
        """Back to PENDING, saying why: the work item will run it again.

        The work item keeps the retry budget; ``charged`` only keeps the
        person's count honest. An outage (``charged=False``) gives back the
        attempt the claim counted, because nothing was learned about these
        bytes; a failure on the document keeps it.
        """
        values: dict[str, object] = {
            "status": FileStatus.PENDING.value,
            "last_processing_error": note,
        }
        if not charged:
            values["processing_attempts"] = func.greatest(
                DatastoreFile.processing_attempts - 1, 0
            )
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_claim == claim,
            )
            .values(values)
        )
        return result.rowcount > 0

    async def mark_failed_permanent_for(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        error: str,
        claim: int | None = None,
    ) -> bool:
        """The terminal failure, for these bytes only.

        Without a claim token -- preflight, or a work item that died -- it
        applies while the row still holds the same bytes and has not finished,
        so it can never overwrite a newer upload's PENDING or a COMPLETED.
        """
        conditions = [
            DatastoreFile.id == file_id,
            DatastoreFile.status.in_(
                (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
            ),
            _content_identity_matches(content_sha256),
        ]
        if claim is not None:
            conditions.append(DatastoreFile.processing_claim == claim)
        result = await self.session.execute(
            update(DatastoreFile)
            .where(*conditions)
            .values(
                status=FileStatus.FAILED_PERMANENT.value,
                last_processing_error=error,
            )
        )
        return result.rowcount > 0

    async def last_processing_error(self, file_id: UUID) -> str | None:
        return await self.session.scalar(
            select(DatastoreFile.last_processing_error).where(
                DatastoreFile.id == file_id
            )
        )

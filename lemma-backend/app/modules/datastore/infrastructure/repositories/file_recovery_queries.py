"""Recovery and fair-dispatch queries for datastore files.

Split out of ``file_repository.py`` to keep that module under the architecture
ratchet's size limit. These are the queries that decide *which* files get worked
on next — the fair, round-robin dispatch of the PENDING backlog, and the
recovery sweep that re-drives or terminally fails stranded rows — as opposed to
the per-file CRUD and lifecycle transitions next door.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update

from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileStatus,
)
from app.modules.datastore.infrastructure.models import DatastoreFile


class DatastoreFileRecoveryQueriesMixin:
    """Scheduling-side queries mixed into ``DatastoreFileRepository``.

    A mixin rather than a separate collaborator because callers (the recovery
    and dispatch services) hold a single repository object and these queries
    share its session.
    """

    async def count_active_for_pod(self, pod_id: UUID) -> int:
        """Files this pod currently has queued or mid-flight (PENDING+PROCESSING).

        The admission gate reads this so one tenant cannot fill the ingestion
        queue with a thousand uploads and push everyone else behind them.
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(DatastoreFile)
                .where(
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.kind == "FILE",
                    DatastoreFile.search_enabled == True,  # noqa: E712
                    DatastoreFile.status.in_(
                        (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
                    ),
                )
            )
            or 0
        )

    async def list_pending_dispatch_candidates(
        self,
        *,
        per_pod_limit: int,
        global_limit: int,
    ) -> Sequence[DatastoreFileEntity]:
        """PENDING files to dispatch next, fairly spread across pods.

        Ingestion is a shared resource. Plain FIFO lets one pod that uploads a
        thousand papers occupy the queue until it drains, so every other tenant
        waits behind it. Ranking within each pod and taking the first
        ``per_pod_limit`` from each gives round-robin service: the bulk uploader
        still drains, just never exclusively.

        Oldest-first within a pod keeps individual files from starving, and the
        global cap is what bounds how much ever sits in Redis at once — the
        PENDING row is the durable backlog, so anything not taken this tick is
        simply picked up on the next one.
        """
        if per_pod_limit <= 0 or global_limit <= 0:
            return []

        ranked = (
            select(
                DatastoreFile.id.label("id"),
                func.row_number()
                .over(
                    partition_by=DatastoreFile.pod_id,
                    order_by=DatastoreFile.created_at.asc(),
                )
                .label("rank"),
            )
            .where(
                DatastoreFile.kind == "FILE",
                DatastoreFile.search_enabled == True,  # noqa: E712
                DatastoreFile.status == FileStatus.PENDING.value,
            )
            .subquery()
        )
        eligible = (
            select(ranked.c.id)
            .where(ranked.c.rank <= per_pod_limit)
            .limit(global_limit)
            .subquery()
        )
        result = await self.session.execute(
            select(DatastoreFile)
            .join(eligible, DatastoreFile.id == eligible.c.id)
            .order_by(DatastoreFile.created_at.asc())
        )
        return [instance.to_entity() for instance in result.scalars().all()]

    async def list_stale_recovery_candidates(
        self,
        *,
        pending_cutoff: datetime,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
    ) -> Sequence[DatastoreFileEntity]:
        # The attempt cap applies to EVERY re-drive branch, not just FAILED.
        # A worker that is OOM-killed / SIGKILLed mid-extraction never runs its
        # mark_failed handler, so the row is stranded in PROCESSING with an
        # incremented processing_attempts. Without the cap here, the PROCESSING
        # branch would re-drive that poison file forever (the failure mode that
        # took the dev worker down). Capping all branches makes the budget real.
        branches = [
            and_(
                DatastoreFile.status == FileStatus.PENDING.value,
                DatastoreFile.updated_at < pending_cutoff,
                DatastoreFile.processing_attempts < max_attempts,
            ),
            and_(
                DatastoreFile.status == FileStatus.PROCESSING.value,
                DatastoreFile.updated_at < processing_cutoff,
                DatastoreFile.processing_attempts < max_attempts,
            ),
        ]
        if failed_cutoff is not None:
            # Re-drive FAILED files that haven't exhausted their retry budget.
            branches.append(
                and_(
                    DatastoreFile.status == FileStatus.FAILED.value,
                    DatastoreFile.updated_at < failed_cutoff,
                    DatastoreFile.processing_attempts < max_attempts,
                )
            )
        result = await self.session.execute(
            select(DatastoreFile).where(
                DatastoreFile.kind == "FILE",
                DatastoreFile.search_enabled == True,  # noqa: E712
                or_(*branches),
            )
        )
        return [instance.to_entity() for instance in result.scalars().all()]

    async def list_exhausted_recovery_candidates(
        self,
        *,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
    ) -> Sequence[DatastoreFileEntity]:
        """Stale PROCESSING/FAILED files that have hit the attempt cap.

        These are the counterpart to ``list_stale_recovery_candidates``: instead
        of being re-driven they are transitioned to the terminal FAILED_PERMANENT
        state so the cron stops resurrecting them. PENDING is excluded — a file
        that has never been claimed past the cap shouldn't exist, and a fresh
        upload legitimately resets attempts to 0.
        """
        branches = [
            and_(
                DatastoreFile.status == FileStatus.PROCESSING.value,
                DatastoreFile.updated_at < processing_cutoff,
                DatastoreFile.processing_attempts >= max_attempts,
            ),
        ]
        if failed_cutoff is not None:
            branches.append(
                and_(
                    DatastoreFile.status == FileStatus.FAILED.value,
                    DatastoreFile.updated_at < failed_cutoff,
                    DatastoreFile.processing_attempts >= max_attempts,
                )
            )
        result = await self.session.execute(
            select(DatastoreFile).where(
                DatastoreFile.kind == "FILE",
                DatastoreFile.search_enabled == True,  # noqa: E712
                or_(*branches),
            )
        )
        return [instance.to_entity() for instance in result.scalars().all()]

    async def bulk_update_status(
        self,
        *,
        file_ids: Sequence[UUID],
        status: FileStatus,
    ) -> int:
        if not file_ids:
            return 0
        result = await self.session.execute(
            update(DatastoreFile)
            .where(DatastoreFile.id.in_(list(file_ids)))
            .values(status=status.value)
        )
        return result.rowcount or 0

    async def bulk_mark_failed_permanent(
        self,
        *,
        file_ids: Sequence[UUID],
        error: str,
    ) -> int:
        """Transition files to the terminal FAILED_PERMANENT state with a reason."""
        if not file_ids:
            return 0
        result = await self.session.execute(
            update(DatastoreFile)
            .where(DatastoreFile.id.in_(list(file_ids)))
            .values(
                status=FileStatus.FAILED_PERMANENT.value,
                last_processing_error=error,
            )
        )
        return result.rowcount or 0

    async def mark_failed_permanent(self, file_id: UUID, *, error: str) -> bool:
        """Terminally fail a single file (e.g. too large to process).

        Not guarded on a prior status: this is called pre-claim (file is PENDING)
        for the size guard, but must not clobber a COMPLETED row if a race put one
        there — so exclude COMPLETED explicitly.
        """
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status != FileStatus.COMPLETED.value,
            )
            .values(
                status=FileStatus.FAILED_PERMANENT.value,
                last_processing_error=error,
            )
        )
        return result.rowcount > 0

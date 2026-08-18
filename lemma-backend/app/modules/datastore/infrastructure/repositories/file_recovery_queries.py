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

from app.modules.datastore.domain.file_entities import FileStatus
from app.modules.datastore.domain.file_projections import DispatchableFileRef
from app.modules.datastore.infrastructure.models import DatastoreFile

#: Columns the scheduling sweeps read. Kept as one tuple so the three queries
#: below cannot drift apart from each other or from ``DispatchableFileRef``.
_DISPATCH_COLUMNS = (
    DatastoreFile.id,
    DatastoreFile.pod_id,
    DatastoreFile.status,
    DatastoreFile.file_metadata,
)


def _to_refs(rows) -> list[DispatchableFileRef]:
    return [
        DispatchableFileRef(
            id=row.id,
            pod_id=row.pod_id,
            status=FileStatus(row.status),
            metadata=row.file_metadata,
        )
        for row in rows
    ]


class DatastoreFileRecoveryQueriesMixin:
    """Scheduling-side queries mixed into ``DatastoreFileRepository``.

    A mixin rather than a separate collaborator because callers (the recovery
    and dispatch services) hold a single repository object and these queries
    share its session.

    Every query here is bounded and projected. These run on crons — one of them
    every minute — against a table nothing prunes, so an unbounded result set is
    not a slow query, it is a worker that dies once the backlog is large enough.
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

    async def count_failed_for_pod(self, pod_id: UUID) -> int:
        """Files this pod tried to index and could not (FAILED + FAILED_PERMANENT).

        The sibling of `count_active_for_pod`, and the half it does not cover.
        Both describe a file that will not answer a search, but only one of them
        resolves itself by waiting -- which is why they are counted separately
        rather than folded into one "not searchable" number that would be told
        to retry.
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
                        (
                            FileStatus.FAILED.value,
                            FileStatus.FAILED_PERMANENT.value,
                        )
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
    ) -> Sequence[DispatchableFileRef]:
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
                *_DISPATCH_COLUMNS,
                DatastoreFile.created_at.label("created_at"),
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
        # Rank first, then age. Without the ordering the global cap took an
        # arbitrary ``global_limit`` rows from the ranked set, so whichever pods
        # the plan happened to emit first won — which is the opposite of what
        # the window is for. Ordering by rank means every pod's first file is
        # served before any pod's second.
        result = await self.session.execute(
            select(
                ranked.c.id,
                ranked.c.pod_id,
                ranked.c.status,
                ranked.c.file_metadata,
            )
            .where(ranked.c.rank <= per_pod_limit)
            .order_by(ranked.c.rank.asc(), ranked.c.created_at.asc())
            .limit(global_limit)
        )
        return _to_refs(result.all())

    async def list_stale_recovery_candidates(
        self,
        *,
        pending_cutoff: datetime,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
        limit: int = 500,
    ) -> Sequence[DispatchableFileRef]:
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
        # Bounded and oldest-first. Unbounded, one stranded ingestion batch is
        # loaded into a single worker in one result set -- the sweep meant to
        # clear a backlog becomes the thing that dies from it. Anything past the
        # limit is simply picked up on the next tick; the row is the durable
        # backlog, not this list.
        result = await self.session.execute(
            select(*_DISPATCH_COLUMNS)
            .where(
                DatastoreFile.kind == "FILE",
                DatastoreFile.search_enabled == True,  # noqa: E712
                or_(*branches),
            )
            .order_by(DatastoreFile.updated_at.asc(), DatastoreFile.id.asc())
            .limit(limit)
        )
        return _to_refs(result.all())

    async def list_exhausted_recovery_candidates(
        self,
        *,
        processing_cutoff: datetime,
        failed_cutoff: datetime | None = None,
        max_attempts: int = 3,
        limit: int = 500,
    ) -> Sequence[DispatchableFileRef]:
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
            select(*_DISPATCH_COLUMNS)
            .where(
                DatastoreFile.kind == "FILE",
                DatastoreFile.search_enabled == True,  # noqa: E712
                or_(*branches),
            )
            .order_by(DatastoreFile.updated_at.asc(), DatastoreFile.id.asc())
            .limit(limit)
        )
        return _to_refs(result.all())

    async def bulk_update_status(
        self,
        *,
        file_ids: Sequence[UUID],
        status: FileStatus,
    ) -> int:
        """Reset a batch of stranded files, without clobbering a finished one.

        The candidate list was read in an earlier statement, so a worker that
        finishes between that SELECT and this UPDATE has already written
        COMPLETED. Without the guard the sweep would drag that row back to
        PENDING and re-extract a document that was done — the single-row
        ``mark_failed_permanent`` below already fences exactly this way.
        """
        if not file_ids:
            return 0
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id.in_(list(file_ids)),
                DatastoreFile.status != FileStatus.COMPLETED.value,
            )
            .values(status=status.value)
        )
        return result.rowcount or 0

    async def bulk_mark_failed_permanent(
        self,
        *,
        file_ids: Sequence[UUID],
        error: str,
    ) -> int:
        """Transition files to the terminal FAILED_PERMANENT state with a reason.

        COMPLETED is excluded for the same reason as ``bulk_update_status``: the
        candidates were selected in an earlier statement, and stamping a file
        that finished in the meantime as permanently failed is worse than
        missing it -- the next sweep will not see it either way.
        """
        if not file_ids:
            return 0
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id.in_(list(file_ids)),
                DatastoreFile.status != FileStatus.COMPLETED.value,
            )
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

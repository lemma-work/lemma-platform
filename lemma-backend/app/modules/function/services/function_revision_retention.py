"""Delete the artifacts of function revisions retention no longer keeps.

Every code save builds a fresh artifact and nothing ever removed one, so a
function's storage grew by a whole dependency-bearing archive on every save.
The decision about WHAT to delete is :mod:`app.core.retention`; this owns doing
it safely.

Two phases, as the pool discipline requires: a short unit of work selects the
revisions and stamps ``pruned_at``, then the object deletes run with no pooled
connection held. Stamping first means a sweep that dies midway leaves rows that
correctly say "build removed" rather than rows that still offer to run.
"""

from __future__ import annotations

from app.modules.function.config import revision_settings

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from obstore.exceptions import BaseError as ObstoreBaseError
from sqlalchemy.exc import SQLAlchemyError

from app.core.retention import RetentionPolicy, select_prunable
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRevisionEntity,
)
from app.modules.function.domain.ports import (
    FunctionRepositoryPort,
    FunctionStorageFactoryPort,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


# How pruning actually fails: the database it selects from, the object store it
# deletes through, and the local filesystem behind a local store. Named rather
# than caught as `Exception` so a genuine defect -- a TypeError in the plan, a
# bad assumption -- still reaches the worker log instead of being reported as a
# degraded sweep and forgotten.
PRUNE_FAILURES = (SQLAlchemyError, ObstoreBaseError, OSError)


def revision_retention_policy() -> RetentionPolicy:
    return RetentionPolicy(
        keep_last=revision_settings.function_revision_keep_last,
        keep_days=revision_settings.function_revision_keep_days,
        max_keep=revision_settings.function_revision_max_keep,
    )


@dataclass(frozen=True, slots=True)
class RevisionPrunePlan:
    function_id: UUID
    artifact_paths: tuple[str, ...]
    source_prefixes: tuple[str, ...]
    revision_numbers: tuple[int, ...]
    version_ids: tuple[UUID, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.artifact_paths and not self.source_prefixes


def _unfinished_prunes(
    revisions: list[FunctionRevisionEntity],
    live_id: UUID | None,
    moment: datetime,
) -> list[FunctionRevisionEntity]:
    """Retry every incomplete deletion, regardless of the age of its tombstone."""
    return [
        revision
        for revision in revisions
        if revision.pruned_at is not None
        and revision.id != live_id
        and revision.purged_at is None
    ]


class FunctionRevisionRetention:
    def __init__(
        self,
        function_repository: FunctionRepositoryPort,
        storage_factory: FunctionStorageFactoryPort,
    ):
        self.repository = function_repository
        self.storage_factory = storage_factory

    async def plan(
        self,
        function: FunctionEntity,
        *,
        policy: RetentionPolicy | None = None,
        now: datetime | None = None,
    ) -> RevisionPrunePlan:
        assert function.id is not None
        function = await self.repository.get_for_update(function.id)
        if function is None:
            raise ValueError("function was deleted before retention could lock it")
        moment = now or datetime.now(timezone.utc)
        revisions = await self.repository.list_revisions(function.id)
        live_id = next(
            (
                revision.id
                for revision in revisions
                if not revision.is_pruned
                and revision.revision_hash == function.revision_hash
            ),
            None,
        )
        candidates = select_prunable(
            revisions,
            policy=policy or revision_retention_policy(),
            live_id=live_id,
            now=moment,
        )
        candidates = await self._drop_in_flight(function.id, candidates, moment)
        unfinished = _unfinished_prunes(revisions, live_id, moment)
        doomed = candidates + unfinished
        if not doomed:
            return RevisionPrunePlan(function.id, (), (), ())

        if candidates:
            await self.repository.mark_revisions_pruned([r.id for r in candidates])
        return RevisionPrunePlan(
            function_id=function.id,
            version_ids=tuple(r.id for r in doomed if r.id is not None),
            artifact_paths=tuple(r.artifact_path for r in doomed),
            # `revisions/<hash>/function.py` -- delete the directory, not the
            # single file, so nothing is left behind if the layout ever grows.
            source_prefixes=tuple(
                r.code_path.rsplit("/", 1)[0] for r in doomed if "/" in r.code_path
            ),
            # Only the fresh ones: re-running a delete is not a new prune.
            revision_numbers=tuple(r.revision_number for r in candidates),
        )

    async def _drop_in_flight(
        self,
        function_id: UUID,
        candidates: list[FunctionRevisionEntity],
        now: datetime,
    ) -> list[FunctionRevisionEntity]:
        """The parent lock fences run creation until these tombstones commit."""
        if not candidates:
            return candidates
        in_flight = await self.repository.revision_hashes_with_runs_in_flight(
            function_id
        )
        return [
            revision
            for revision in candidates
            if revision.revision_hash not in in_flight
        ]

    async def execute(self, plan: RevisionPrunePlan) -> None:
        """Storage phase: delete the bytes. Holds NO DB connection."""
        if plan.is_empty:
            return
        storage = self.storage_factory(plan.function_id)
        for path in plan.artifact_paths:
            try:
                await storage.delete_file(path)
            except FileNotFoundError:
                continue
        for prefix in plan.source_prefixes:
            # Never a bare prefix: that would delete the whole function's
            # storage, including the live revision's artifact.
            if not prefix:
                continue
            await storage.delete_prefix(prefix)
        logger.info(
            "function.function_revision_retention.revisions_pruned",
            function_id=str(plan.function_id),
            pruned_count=len(plan.revision_numbers),
        )

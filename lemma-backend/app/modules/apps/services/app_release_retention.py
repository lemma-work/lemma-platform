"""Delete the bytes of app releases retention no longer keeps.

Nothing has ever removed a release, so an app's storage grew by a whole dist on
every deploy, forever. The decision about WHAT to delete is
:mod:`app.core.retention`; this owns doing it safely.

Two phases, in the order the pool discipline requires: a short unit of work
selects the releases and stamps ``pruned_at``, then the object deletes happen
with no pooled connection held. Stamping first is deliberate -- a sweep that
dies midway must not leave a release the UI still offers to promote and whose
bytes are half gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from obstore.exceptions import BaseError as ObstoreBaseError
from sqlalchemy.exc import SQLAlchemyError

from app.core.retention import RetentionPolicy, select_prunable
from app.modules.apps.config import apps_settings
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity
from app.modules.apps.domain.ports import AppRepositoryPort, AppStorageFactoryPort
from app.core.log.log import get_logger

logger = get_logger(__name__)


# How pruning actually fails: the database it selects from, the object store it
# deletes through, and the local filesystem behind a local store. Named rather
# than caught as `Exception` so a genuine defect -- a TypeError in the plan, a
# bad assumption -- still reaches the worker log instead of being reported as a
# degraded sweep and forgotten.
PRUNE_FAILURES = (SQLAlchemyError, ObstoreBaseError, OSError)


def release_retention_policy() -> RetentionPolicy:
    return RetentionPolicy(
        keep_last=apps_settings.app_release_keep_last,
        keep_days=apps_settings.app_release_keep_days,
        max_keep=apps_settings.app_release_max_keep,
    )


@dataclass(frozen=True, slots=True)
class ReleasePrunePlan:
    """What to delete, carried out of the unit of work that marked it."""

    app_id: UUID
    dist_roots: tuple[str, ...]
    dist_archives: tuple[str, ...]
    source_archives: tuple[str, ...]
    release_numbers: tuple[int, ...]
    version_ids: tuple[UUID, ...] = ()

    @property
    def is_empty(self) -> bool:
        # All three, not two. A release whose archive sits outside its own root
        # -- an empty `dist_root_path`, which the column permits and the backfill
        # can produce -- lands in `dist_archives` and nowhere else, so omitting
        # it here made `execute` return early and leak exactly those bytes.
        return not (self.dist_roots or self.dist_archives or self.source_archives)


def _prunable_source_paths(
    prunable: list[AppReleaseEntity],
    retained: list[AppReleaseEntity],
    app: AppEntity,
) -> set[str]:
    """Source blobs no retained release (or the app row) still points at.

    Legacy releases may share a source path. Deleting that path while another
    release retains it would remove code that is still available for rollback.
    """
    still_referenced = {
        release.source_archive_path
        for release in retained
        if release.source_archive_path
    }
    if app.source_archive_path:
        still_referenced.add(app.source_archive_path)
    return {
        release.source_archive_path
        for release in prunable
        if release.source_archive_path
        and release.source_archive_path not in still_referenced
    }


def _unfinished_prunes(
    releases: list[AppReleaseEntity], app: AppEntity, moment: datetime
) -> list[AppReleaseEntity]:
    """Retry every incomplete deletion, regardless of the age of its tombstone."""
    return [
        release
        for release in releases
        if release.pruned_at is not None
        and release.id != app.current_release_id
        and release.purged_at is None
    ]


def _prune_paths(
    app_id: UUID,
    doomed: list[AppReleaseEntity],
    retained: list[AppReleaseEntity],
    app: AppEntity,
    counted: list[AppReleaseEntity],
) -> ReleasePrunePlan:
    """Turn the chosen releases into the paths whose bytes go."""
    return ReleasePrunePlan(
        app_id=app_id,
        version_ids=tuple(r.id for r in doomed if r.id is not None),
        dist_roots=tuple(r.dist_root_path for r in doomed if r.dist_root_path),
        dist_archives=tuple(
            r.dist_archive_path
            for r in doomed
            # An archive stored inside its own release root goes with the
            # prefix delete; only one outside it needs its own call.
            if r.dist_archive_path
            and not r.dist_archive_path.startswith(r.dist_root_path or "\0")
        ),
        source_archives=tuple(_prunable_source_paths(doomed, retained, app)),
        release_numbers=tuple(
            r.release_number for r in counted if r.release_number is not None
        ),
    )


class AppReleaseRetention:
    def __init__(
        self,
        app_repository: AppRepositoryPort,
        file_manager_factory: AppStorageFactoryPort,
    ):
        self.repository = app_repository
        self.file_manager_factory = file_manager_factory

    async def plan(
        self,
        app: AppEntity,
        *,
        policy: RetentionPolicy | None = None,
        now: datetime | None = None,
    ) -> ReleasePrunePlan:
        """DB phase: choose the releases to prune and stamp them.

        Returns the storage paths so the caller can delete them after this unit
        of work commits.
        """
        assert app.id is not None
        app = await self.repository.get_for_update(app.id)
        if app is None:
            raise ValueError("app was deleted before retention could lock it")
        moment = now or datetime.now(timezone.utc)
        releases = await self.repository.list_releases(app.id)
        prunable = select_prunable(
            releases,
            policy=policy or release_retention_policy(),
            live_id=app.current_release_id,
            now=moment,
        )
        unfinished = _unfinished_prunes(releases, app, moment)
        doomed = prunable + unfinished
        if not doomed:
            return ReleasePrunePlan(app.id, (), (), (), ())

        doomed_ids = {release.id for release in doomed}
        retained = [release for release in releases if release.id not in doomed_ids]
        # Only the freshly selected ones are stamped, and only they are counted:
        # re-running a delete is not a new prune.
        if prunable:
            await self.repository.mark_releases_pruned([r.id for r in prunable])
        return _prune_paths(app.id, doomed, retained, app, prunable)

    async def execute(self, plan: ReleasePrunePlan) -> None:
        """Storage phase: delete the bytes. Holds NO DB connection."""
        if plan.is_empty:
            return
        storage = self.file_manager_factory(plan.app_id)
        for dist_root in plan.dist_roots:
            # Never a bare prefix: on a bucket-root store that would wipe the
            # bucket rather than one release.
            if not dist_root:
                continue
            await storage.delete_prefix(dist_root)
        for path in (*plan.dist_archives, *plan.source_archives):
            try:
                await storage.delete_file(path)
            except FileNotFoundError:
                continue
        logger.info(
            "apps.app_release_retention.releases_pruned",
            app_id=str(plan.app_id),
            pruned_count=len(plan.release_numbers),
        )

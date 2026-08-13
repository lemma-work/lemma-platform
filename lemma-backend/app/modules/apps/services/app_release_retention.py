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

from app.core.retention import RetentionPolicy, select_prunable
from app.modules.apps.config import apps_settings
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity
from app.modules.apps.domain.ports import AppRepositoryPort, AppStorageFactoryPort
from app.core.log.log import get_logger

logger = get_logger(__name__)


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

    @property
    def is_empty(self) -> bool:
        return not self.dist_roots and not self.source_archives


def _prunable_source_paths(
    prunable: list[AppReleaseEntity],
    retained: list[AppReleaseEntity],
    app: AppEntity,
) -> set[str]:
    """Source blobs no retained release (or the app row) still points at.

    Source is content-addressed at ``source/<sha>/archive.zip``, so two releases
    built from identical source SHARE one blob -- a dist-only change produces a
    new release with the same source path. Deleting that blob because the older
    release was pruned would strip the source from a release that is still
    listed, and possibly from the live one.
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
        releases = await self.repository.list_releases(app.id)
        prunable = select_prunable(
            releases,
            policy=policy or release_retention_policy(),
            live_id=app.current_release_id,
            now=now or datetime.now(timezone.utc),
        )
        if not prunable:
            return ReleasePrunePlan(app.id, (), (), (), ())

        pruned_ids = {release.id for release in prunable}
        retained = [release for release in releases if release.id not in pruned_ids]
        await self.repository.mark_releases_pruned([r.id for r in prunable])
        return ReleasePrunePlan(
            app_id=app.id,
            dist_roots=tuple(r.dist_root_path for r in prunable if r.dist_root_path),
            dist_archives=tuple(
                r.dist_archive_path
                for r in prunable
                # An archive stored inside its own release root goes with the
                # prefix delete; only one outside it needs its own call.
                if r.dist_archive_path
                and not r.dist_archive_path.startswith(r.dist_root_path or "\0")
            ),
            source_archives=tuple(_prunable_source_paths(prunable, retained, app)),
            release_numbers=tuple(
                r.release_number for r in prunable if r.release_number is not None
            ),
        )

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

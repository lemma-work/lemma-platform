"""Storage + search-index side of the file write/delete sagas.

``FileStoragePhase`` is constructed with ONLY storage/search collaborators — a
storage port, a search-service factory, a repo-free ``FileProjection``, and the
(pure) path resolver. It holds **no file repository**, so it is impossible to
issue a DB query from here: the methods touch only object storage and the search
index (which has its own pool). They take plain dataclasses (``_UpdatePlan`` /
``_PathDeletionCleanup``) carrying everything resolved during the DB phase, so
they are safe to run *after* the resolving Unit of Work has closed, holding no
pooled DB connection.

The DB→storage hand-off dataclasses live here (their natural home) so the writer
and the storage phase can share them without an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Callable
from uuid import UUID

from app.core.api.uploads import upload_source_sha256
from app.core.concurrency.offload import run_blocking
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreDomainError,
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
)
from app.modules.datastore.domain.indexing_policy import is_indexable_mime_type
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_file_storage_key,
)
from app.modules.datastore.domain.ports import (
    DatastoreSearchFactoryPort,
    DatastoreStoragePort,
)
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_child_container_prefix,
)
from app.modules.datastore.services.files.projection import FileProjection

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _PathDeletionCleanup:
    """Storage + search-index cleanup for already-deleted file/folder rows,
    carried out of the short UoW so the purge holds no pooled connection. The
    ``files`` entries (file rows only) are plain dicts so the payload is
    JSON-serializable for an offloaded worker task."""

    pod_id: UUID
    is_folder: bool
    folder_prefix: str | None
    files: tuple


@dataclass(frozen=True, slots=True)
class _StorageMove:
    source_key: str
    destination_key: str


@dataclass(frozen=True, slots=True)
class _ArtifactMove:
    """A file's derived artifacts following its bytes to a new path.

    Converted markdown, extracted figures, the manifest and any cached page
    renders live in a hidden container beside the file, keyed by its path. A
    rename used to delete that container and mark the row for reprocessing, so
    renaming a folder re-extracted every document under it -- OCR included --
    to arrive at artifacts identical to the ones just deleted.

    They are a pure function of the bytes, and the bytes are being moved. So
    they move too, and nothing is re-extracted.
    """

    file_id: UUID
    source_prefix: str
    destination_prefix: str


def plan_artifact_moves(
    *,
    file_entity: DatastoreFileEntity,
    descendants: list[DatastoreFileEntity],
    previous_path: str,
    has_content: bool,
) -> list[_ArtifactMove]:
    """Which derived containers a rename can carry across, and which it cannot.

    The rule is the file's **name**, not its path. Artifacts describe the bytes
    as read through a particular filename -- `report.pdf` extracts differently
    from `report.docx` -- so a rename that changes the name invalidates them and
    they are deleted and regenerated, exactly as before. A rename that only
    moves a file, or renames the folder above it, leaves every name intact,
    which is precisely the case that used to cost the most.

    A content update plans nothing: new bytes genuinely need new artifacts.
    """
    if has_content or previous_path == file_entity.path:
        return []

    moves: list[_ArtifactMove] = []

    def carry(entity: DatastoreFileEntity, was_at: str, now_at: str) -> None:
        if not entity.is_file or _file_name(was_at) != _file_name(now_at):
            return
        moves.append(
            _ArtifactMove(
                file_id=entity.id,
                source_prefix=build_datastore_child_container_prefix(
                    entity.pod_id, was_at
                ),
                destination_prefix=build_datastore_child_container_prefix(
                    entity.pod_id, now_at
                ),
            )
        )

    carry(file_entity, previous_path, file_entity.path)
    if file_entity.is_folder:
        for descendant in descendants:
            suffix = descendant.path.removeprefix(previous_path)
            carry(descendant, descendant.path, f"{file_entity.path}{suffix}")
    return moves


def _file_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def plan_storage_moves(
    *,
    projection: FileProjection,
    file_entity: DatastoreFileEntity,
    descendants: list[DatastoreFileEntity],
    previous_path: str,
    previous_storage_key: str | None,
    new_storage_key: str | None,
    has_content: bool,
) -> list[_StorageMove]:
    """Which blobs a rename has to copy, decided before any DB row is written.

    Pure key arithmetic over already-loaded entities -- it lives here, beside
    the phase that executes the moves, because it needs no repository and the
    writer that calls it is the one place that must not grow another one.

    A content update writes a fresh key and needs no move; a rename of a file
    moves its own blob, and a rename of a folder moves every file beneath it.
    """
    moves: list[_StorageMove] = []
    if not has_content and previous_storage_key and new_storage_key:
        if previous_storage_key != new_storage_key:
            moves.append(_StorageMove(previous_storage_key, new_storage_key))
    if previous_path == file_entity.path or not file_entity.is_folder:
        return moves
    for descendant in descendants:
        if not descendant.is_file:
            continue
        suffix = descendant.path.removeprefix(previous_path)
        destination_path = f"{file_entity.path}{suffix}"
        moves.append(
            _StorageMove(
                projection.storage_key(descendant),
                build_datastore_file_storage_key(descendant.pod_id, destination_path),
            )
        )
    return moves


@dataclass(frozen=True, slots=True)
class _UpdatePlan:
    """DB-resolved + in-memory-mutated state for a file update, carried across the
    storage write so the byte move/upload + search sync hold no connection."""

    file_entity: DatastoreFileEntity
    previous_path: str
    previous_search_enabled: bool
    previous_storage_key: str | None
    new_storage_key: str | None
    has_content: bool
    rename_moved: bool
    storage_moves: tuple[_StorageMove, ...]
    artifact_moves: tuple[_ArtifactMove, ...]
    #: The folder's descendants as they were *before* the rename. Carried so the
    #: index can be told each one's new path without reading them back, and
    #: empty for anything that is not a folder rename.
    renamed_descendants: tuple[DatastoreFileEntity, ...]
    should_sync: bool
    requester_user_id: UUID
    #: File ids whose derived artifacts could not be carried across. Mutable on
    #: a frozen dataclass the way `file_entity` already is: the storage phase
    #: fills it in, and the DB phase that follows marks exactly those rows for
    #: reprocessing -- which is what the old code did to every row
    #: unconditionally.
    artifacts_left_behind: set[UUID] = field(default_factory=set)


class FileStoragePhase:
    """Storage + search side of file sagas. No repository — DB-free by
    construction."""

    def __init__(
        self,
        storage: DatastoreStoragePort,
        search_factory_provider: Callable[[], DatastoreSearchFactoryPort],
        projection: FileProjection,
        path_resolver: PathResolver,
    ):
        self.storage = storage
        self._search_factory_provider = search_factory_provider
        self.projection = projection
        self.paths = path_resolver

    async def write_update(
        self, plan: _UpdatePlan, update_entity: DatastoreFileUpdateEntity
    ) -> None:
        """Upload new content / move the blob to its new key. Holds NO DB
        connection. The old blob is deleted later (finalize) — after the row is
        persisted — so a mid-flight failure can only orphan a blob, never lose
        data."""
        if plan.has_content:
            if plan.new_storage_key is None or update_entity.content is None:
                raise DatastoreInfrastructureError(
                    "Content update is missing its immutable storage target"
                )
            # Hash the bytes here rather than during the resolve phase: it is
            # CPU on a worker thread, proportional to the file, and the resolve
            # phase runs inside the caller's unit of work -- this one line was
            # what made its "DB only" docstring untrue. `persist_update_file`
            # runs after this phase, so the row still carries the digest.
            plan.file_entity.content_sha256 = await run_blocking(
                upload_source_sha256, update_entity.content
            )
            try:
                await self.storage.upload_file(
                    plan.new_storage_key, update_entity.content
                )
                stored_size = await self.storage.stat_file(plan.new_storage_key)
                if stored_size != plan.file_entity.size_bytes:
                    raise DatastoreInfrastructureError(
                        "Uploaded object size does not match staged content"
                    )
            except DatastoreDomainError as exc:
                raise DatastoreInfrastructureError(
                    "Failed to upload updated file content"
                ) from exc
        elif plan.storage_moves:
            copied: list[_StorageMove] = []
            try:
                for move in plan.storage_moves:
                    await self.storage.copy_file(move.source_key, move.destination_key)
                    copied.append(move)
            except DatastoreDomainError as exc:
                for move in reversed(copied):
                    try:
                        await self.storage.delete_file(move.destination_key)
                    except DatastoreDomainError:
                        logger.debug(
                            "datastore.storage_phase.rolling_back_staged_move_s.diagnostic",
                            exc_info=True,
                        )
                raise DatastoreInfrastructureError(
                    "Failed to move file content after rename"
                ) from exc

        # Outside both branches rather than inside the rename one: the planner
        # already returns nothing for a content update or a non-rename, and
        # tying this to `storage_moves` being non-empty would silently skip a
        # folder whose files all sit deeper than one level.
        await self._carry_artifacts(plan)

    async def _carry_artifacts(self, plan: _UpdatePlan) -> None:
        """Move each file's derived container, noting the ones that would not go.

        Best-effort, and deliberately not part of the rollback above: a derived
        artifact is regenerable and the file's own bytes are not, so a figure
        that failed to copy must not cost somebody their rename. What it costs
        instead is one reprocess -- the row is marked, `_update_requires_sync`
        sees it, and the file re-extracts exactly as it used to.

        Before the DB phase, because that phase is where the mark has to land.
        """
        for move in plan.artifact_moves:
            try:
                await self.storage.move_prefix(
                    move.source_prefix, move.destination_prefix
                )
            except DatastoreDomainError:
                plan.artifacts_left_behind.add(move.file_id)
                logger.debug(
                    "datastore.storage_phase.carrying_child_artifacts_s.diagnostic",
                    file_id=str(move.file_id),
                    exc_info=True,
                )

    async def finalize_update(
        self, plan: _UpdatePlan, updated_entity: DatastoreFileEntity
    ) -> None:
        """Storage + search-index sync after the row is persisted. Holds NO DB
        connection; best-effort throughout (orphans are swept, never torn rows)."""
        # Delete the old blob only now that the row points at the new key.
        if (
            plan.has_content
            and plan.previous_storage_key
            and plan.previous_storage_key != plan.new_storage_key
        ):
            with suppress(DatastoreDomainError):
                await self.storage.delete_file(plan.previous_storage_key)

        await self._delete_move_sources(plan.storage_moves)

        # Synchronous chunk + converted-artifact cleanup when a file is (or has
        # become) unsearchable — search disabled OR a non-indexable type (e.g.
        # after a rename changed its extension). This must NOT depend on the
        # reindex queue: the queue only enqueues PENDING + search_enabled files,
        # so a disabled/NOT_REQUIRED file is never processed and any previously
        # indexed chunks would otherwise be left stale. The removal is idempotent
        # (a no-op when there are no chunks), so it is also safe on a plain
        # non-indexable update. Keys use the file's CURRENT path (post-rename),
        # matching where storage/projection artifacts live after a move.
        search_service = self._search_factory_provider()(updated_entity.pod_id)
        if updated_entity.is_file and (
            not updated_entity.search_enabled
            or not is_indexable_mime_type(updated_entity.mime_type, updated_entity.name)
        ):
            try:
                await search_service.remove_file(updated_entity.id)
            except Exception:
                # Stale chunks keep the file findable after the user made it
                # unsearchable. The row is correct and a reindex repairs it, so
                # degraded rather than failed — but it is user-visible.
                logger.warning(
                    "datastore.storage_phase.search_index_purge_unsearchable.degraded",
                    file_id=str(updated_entity.id),
                    exc_info=True,
                )
            await self.projection.delete_child_artifacts(
                updated_entity.pod_id,
                updated_entity.path,
            )

        if plan.previous_path != updated_entity.path:
            await self._sync_renamed_paths(plan, updated_entity, search_service)

    async def _sync_renamed_paths(
        self,
        plan: _UpdatePlan,
        updated_entity: DatastoreFileEntity,
        search_service,
    ) -> None:
        """Tell the index where things are now, and clear what did not move.

        The index keys chunks by file id, so a rename is a metadata correction
        rather than a reindex -- which is the whole reason a rename no longer
        needs to re-extract anything. The renamed file already got this; its
        descendants did not, so a renamed folder left every file beneath it
        claiming its old path in search results.

        The old container is only swept for files whose artifacts stayed behind.
        Sweeping it unconditionally would delete the container `move_prefix`
        just wrote when a file's name is unchanged -- source and destination
        differ, but a failed move leaves the source where a successful one does
        not.
        """
        update_file_path = getattr(search_service, "update_file_path", None)
        carried = {move.file_id for move in plan.artifact_moves}

        # `(entity, where it is now, where it was)`. The descendants in the plan
        # were read before the rename and still carry their old paths, so the
        # new one is derived rather than read back.
        renamed = [(updated_entity, updated_entity.path, plan.previous_path)]
        for descendant in plan.renamed_descendants:
            suffix = descendant.path.removeprefix(plan.previous_path)
            renamed.append(
                (descendant, f"{updated_entity.path}{suffix}", descendant.path)
            )

        for entity, now_at, was_at in renamed:
            if (
                entity.is_file
                and entity.search_enabled
                and update_file_path is not None
            ):
                with suppress(Exception):
                    await update_file_path(
                        entity.id, now_at, self.paths._parent_path(now_at)
                    )
            # Folders have no derived container, so there was never anything at
            # their old path to sweep -- the call was a no-op issued once per
            # rename.
            if entity.is_file and (
                entity.id not in carried or entity.id in plan.artifacts_left_behind
            ):
                await self.projection.delete_child_artifacts(entity.pod_id, was_at)

    async def _purge_search_entries(self, search_service, files: Sequence) -> None:
        """Drop a deleted folder's chunks, in one transaction rather than N.

        Per-file removal opens its own session and commits, so deleting five
        hundred files opened five hundred sessions -- on the path that runs
        *after* the rows are gone, where a failure means deleted content stays
        searchable and retrievable by an agent. One statement also means the
        folder's chunks go together or not at all.

        `remove_files` is on the port, so a search backend either has it or is
        not one. The single handler below is the same one the per-file loop had:
        this is a data-deletion failure, not a cost, and it is logged as one.
        """
        file_ids = [UUID(item["file_id"]) for item in files]
        if not file_ids:
            return
        try:
            await search_service.remove_files(file_ids)
        except Exception:
            logger.error(
                "datastore.storage_phase.deleted_file_search_purge.failed",
                file_count=len(file_ids),
                exc_info=True,
            )

    async def _delete_move_sources(
        self, storage_moves: tuple[_StorageMove, ...]
    ) -> None:
        for move in storage_moves:
            if move.source_key == move.destination_key:
                continue
            with suppress(DatastoreDomainError):
                await self.storage.delete_file(move.source_key)

    async def cleanup_uncommitted_update(self, plan: _UpdatePlan) -> None:
        """Delete a newly written object when the following DB phase fails.

        The previous key is never touched, so the committed row remains readable.
        """
        if (
            plan.has_content
            and plan.new_storage_key
            and plan.new_storage_key != plan.previous_storage_key
        ):
            try:
                await self.storage.delete_file(plan.new_storage_key)
            except DatastoreObjectNotFoundError:
                return
            except Exception:
                # An orphaned object costs storage; nothing is incorrect.
                logger.warning(
                    "datastore.storage_phase.uncommitted_object_delete.degraded",
                    exc_info=True,
                )
        for move in plan.storage_moves:
            if move.destination_key == move.source_key:
                continue
            try:
                await self.storage.delete_file(move.destination_key)
            except DatastoreObjectNotFoundError:
                continue
            except DatastoreDomainError:
                logger.warning(
                    "datastore.storage_phase.staged_object_delete.degraded",
                    exc_info=True,
                )

    async def cleanup_deleted_paths(
        self,
        pod_id: UUID,
        *,
        is_folder: bool,
        folder_prefix: str | None,
        files: list[dict[str, str]],
    ) -> None:
        """Purge storage bytes + search-index entries for already-deleted rows.
        Holds NO main DB connection (search uses its own pool); call after
        resolve_delete_path's UoW has committed. Best-effort throughout."""
        search_service = self._search_factory_provider()(pod_id)
        if is_folder:
            if folder_prefix:
                with suppress(DatastoreDomainError):
                    await self.storage.delete_prefix(folder_prefix)
            # The folder prefix removes canonical originals and colocated
            # derived children. Exact deletes remain idempotent and cover any
            # cleanup payload produced before a partial folder operation.
            for item in files:
                with suppress(Exception):
                    await self.storage.delete_file(item["storage_key"])
            await self._purge_search_entries(search_service, files)
            return
        for item in files:
            with suppress(Exception):
                await self.storage.delete_file(item["storage_key"])
            await self.projection.delete_child_artifacts(pod_id, item["path"])
        # Through the same helper as the folder branch: one file is the batch of
        # one, and having a single place that can fail to purge a deleted file's
        # chunks is worth more here than saving a list.
        await self._purge_search_entries(search_service, files)

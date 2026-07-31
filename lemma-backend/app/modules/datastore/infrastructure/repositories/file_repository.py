from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, text, update

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.datastore.domain.errors import DatastoreRecordNotFoundError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileStatus,
)
from app.modules.datastore.domain.ports import DatastoreFileRepositoryPort
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories._base import (
    DatastoreRepositoryBase,
)
from app.modules.datastore.infrastructure.sql_identifiers import escape_like


def _direct_child_patterns(directory_path: str) -> tuple[str, str]:
    """LIKE patterns matching a directory's direct children but not deeper."""
    if directory_path == "/":
        return "/%", "/%/%"
    escaped = escape_like(directory_path)
    return f"{escaped}/%", f"{escaped}/%/%"


def _file_actions_expr(ctx: Context):
    return allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=DatastoreFile.id,
        pod_id_col=DatastoreFile.pod_id,
        owner_user_id_col=DatastoreFile.owner_user_id,
        visibility_col=DatastoreFile.visibility,
        resource_path_col=DatastoreFile.path,
    )


def _content_identity_matches(content_sha256: str | None):
    if content_sha256 is None:
        return DatastoreFile.content_sha256.is_(None)
    return DatastoreFile.content_sha256 == content_sha256


def _file_payload(entity: DatastoreFileEntity) -> dict:
    payload = entity.model_dump(exclude={"allowed_actions"})
    payload["kind"] = entity.kind.value
    payload["status"] = entity.status.value
    payload["file_metadata"] = payload.pop("metadata", None)
    return payload


class DatastoreFileRepository(DatastoreRepositoryBase, DatastoreFileRepositoryPort):
    """Persistence for file/folder metadata (the application DB)."""

    async def acquire_path_lock(self, pod_id: UUID, path: str) -> None:
        """Serialize mkdir-p decisions for one pod/path until transaction end."""
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(CAST(:path_key AS text), 0))"
            ),
            {"path_key": f"{pod_id}:{path}"},
        )

    async def create(self, entity: DatastoreFileEntity) -> DatastoreFileEntity:
        instance = DatastoreFile(**_file_payload(entity))
        self.session.add(instance)
        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[DatastoreFileEntity]:
        result = await self.session.execute(
            select(DatastoreFile).where(DatastoreFile.id == id)
        )
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    # --- Indexing-pipeline lifecycle (status as the stored string value) -------
    # These back DatastoreFileProcessingService. Status comparisons use the ORM
    # string value (FileStatus(...).value), so the model — not the enum-typed
    # entity — is the right unit here; the service reads its fields read-only.

    async def get_model(self, file_id: UUID) -> Optional[DatastoreFile]:
        return (
            await self.session.execute(
                select(DatastoreFile).where(DatastoreFile.id == file_id)
            )
        ).scalar_one_or_none()

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
        file_metadata: dict,
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

    async def update(self, entity: DatastoreFileEntity) -> DatastoreFileEntity:
        result = await self.session.execute(
            select(DatastoreFile).where(DatastoreFile.id == entity.id)
        )
        instance = result.scalars().first()
        if not instance:
            raise DatastoreRecordNotFoundError("File not found")

        if (
            instance.visibility == ResourceVisibility.RESTRICTED.value
            and entity.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=entity.pod_id,
                resource_type=ResourceType.DOCUMENT,
                resource_id=entity.id,
            )

        for key, value in _file_payload_unset(entity).items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def delete(self, id: UUID) -> bool:
        result = await self.session.execute(
            delete(DatastoreFile).where(DatastoreFile.id == id)
        )
        return result.rowcount > 0

    async def delete_entity(self, entity: DatastoreFileEntity) -> bool:
        result = await self.session.execute(
            select(DatastoreFile).where(DatastoreFile.id == entity.id)
        )
        instance = result.scalars().first()
        if not instance:
            return False
        self._collect_events(entity)
        await self.session.delete(instance)
        return True

    async def get_by_datastore(
        self,
        pod_id: UUID,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        direct, nested = _direct_child_patterns(directory_path)
        stmt = select(DatastoreFile).where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(direct, escape="!"),
            ~DatastoreFile.path.like(nested, escape="!"),
        )
        if cursor:
            stmt = stmt.where(DatastoreFile.id > UUID(cursor))
        stmt = stmt.order_by(DatastoreFile.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        children = list(result.scalars().all())

        next_cursor = None
        if len(children) > limit:
            next_cursor = str(children[limit - 1].id)
            children = children[:limit]
        return [item.to_entity() for item in children], next_cursor

    async def list_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        direct, nested = _direct_child_patterns(directory_path)
        actions = _file_actions_expr(ctx)
        stmt = select(DatastoreFile, actions).where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(direct, escape="!"),
            ~DatastoreFile.path.like(nested, escape="!"),
            allowed_actions_contains(actions, Permissions.FOLDER_READ),
        )
        if cursor:
            stmt = stmt.where(DatastoreFile.id > UUID(cursor))
        stmt = stmt.order_by(DatastoreFile.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.all())

        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit - 1][0].id)
            rows = rows[:limit]
        return [
            self._with_allowed_actions(item.to_entity(), allowed)
            for item, allowed in rows
        ], next_cursor

    async def get_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context | None = None,
    ) -> Optional[DatastoreFileEntity]:
        if ctx is None:
            result = await self.session.execute(
                select(DatastoreFile).where(
                    DatastoreFile.pod_id == pod_id,
                    DatastoreFile.path == path,
                )
            )
            instance = result.scalars().first()
            return instance.to_entity() if instance else None

        actions = _file_actions_expr(ctx)
        result = await self.session.execute(
            select(DatastoreFile, actions).where(
                DatastoreFile.pod_id == pod_id,
                DatastoreFile.path == path,
            )
        )
        row = result.first()
        return self._with_allowed_actions(row[0].to_entity(), row[1]) if row else None

    async def get_all_by_datastore(
        self,
        pod_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> Sequence[DatastoreFileEntity]:
        stmt = select(DatastoreFile).where(DatastoreFile.pod_id == pod_id)
        if owner_user_id is not None:
            stmt = stmt.where(DatastoreFile.owner_user_id == owner_user_id)
        result = await self.session.execute(stmt.order_by(DatastoreFile.path))
        return [instance.to_entity() for instance in result.scalars().all()]

    async def get_by_paths(
        self,
        pod_id: UUID,
        paths: Sequence[str],
    ) -> Sequence[DatastoreFileEntity]:
        if not paths:
            return []
        result = await self.session.execute(
            select(DatastoreFile)
            .where(
                DatastoreFile.pod_id == pod_id,
                DatastoreFile.path.in_(list(paths)),
            )
            .order_by(DatastoreFile.path)
        )
        return [instance.to_entity() for instance in result.scalars().all()]

    async def filter_visible_ids(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        file_ids: Sequence[UUID],
    ) -> set[UUID]:
        if not file_ids:
            return set()
        actions = _file_actions_expr(ctx)
        result = await self.session.execute(
            select(DatastoreFile.id).where(
                DatastoreFile.pod_id == pod_id,
                DatastoreFile.id.in_(list(file_ids)),
                allowed_actions_contains(actions, Permissions.FOLDER_READ),
            )
        )
        return set(result.scalars().all())

    async def get_descendants(
        self,
        pod_id: UUID,
        path_prefix: str,
    ) -> Sequence[DatastoreFileEntity]:
        result = await self.session.execute(
            select(DatastoreFile)
            .where(
                DatastoreFile.pod_id == pod_id,
                DatastoreFile.path.like(f"{escape_like(path_prefix)}/%", escape="!"),
            )
            .order_by(DatastoreFile.path)
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


def _file_payload_unset(entity: DatastoreFileEntity) -> dict:
    data = entity.model_dump(exclude_unset=True)
    data["kind"] = entity.kind.value
    data["status"] = entity.status.value
    data["file_metadata"] = data.pop("metadata", None)
    return data

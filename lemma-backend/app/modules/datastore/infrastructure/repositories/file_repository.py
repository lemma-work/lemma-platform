from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import (
    and_,
    delete,
    select,
    text,
)

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
)
from app.core.infrastructure.db.transaction_locks import (
    mark_transaction_scoped_lock,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
)
from app.modules.datastore.domain.ports import DatastoreFileRepositoryPort
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_tree_sql import (
    tree_statements,
)
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_unreadable_ancestor,
)
from app.modules.datastore.infrastructure.repositories._base import (
    DatastoreRepositoryBase,
)
from app.modules.datastore.infrastructure.repositories.file_listing_reads import (
    DatastoreFileListingMixin,
)
from app.modules.datastore.infrastructure.repositories.file_processing_state import (
    DatastoreFileProcessingStateMixin,
)
from app.modules.datastore.infrastructure.repositories.file_listing_sql import (
    direct_child_patterns,
    repoint_descendants,
)
from app.modules.datastore.infrastructure.repositories.file_recovery_queries import (
    DatastoreFileRecoveryQueriesMixin,
)


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


def _file_payload(entity: DatastoreFileEntity) -> dict:
    payload = entity.model_dump(exclude={"allowed_actions"})
    payload["kind"] = entity.kind.value
    payload["status"] = entity.status.value
    payload["file_metadata"] = payload.pop("metadata", None)
    return payload


class DatastoreFileRepository(
    DatastoreFileListingMixin,
    DatastoreFileProcessingStateMixin,
    DatastoreFileRecoveryQueriesMixin,
    DatastoreRepositoryBase,
    DatastoreFileRepositoryPort,
):
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
        # This lock dies at commit, so nothing may commit this session until the
        # caller does. Connection-scope releases guard on pending ORM work and
        # would otherwise see a clean session and hand the connection back,
        # taking the mutual exclusion with it.
        mark_transaction_scoped_lock(self.session)

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

    async def delete_entities(self, entities: Sequence[DatastoreFileEntity]) -> int:
        """Delete many rows in one statement, and say how many went.

        The per-entity version reads the row before deleting it, so removing a
        folder of five hundred files issued a thousand statements inside the
        request transaction. The read was there to answer "was it still there",
        which a `DELETE`'s own row count answers without it.

        The caller compares that count against what it asked for, which is the
        staleness check: the ids came from a `SELECT` taken earlier in the same
        transaction, and a short count means the tree moved underneath.
        """
        ids = [entity.id for entity in entities if entity.id is not None]
        if not ids:
            return 0
        for entity in entities:
            self._collect_events(entity)
        result = await self.session.execute(
            delete(DatastoreFile).where(DatastoreFile.id.in_(ids))
        )
        return int(result.rowcount or 0)

    async def get_by_datastore(
        self,
        pod_id: UUID,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        direct, nested = direct_child_patterns(directory_path)
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
        direct, nested = direct_child_patterns(directory_path)
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

    async def visible_file_ids(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk_ancestors: bool,
    ) -> set[UUID]:
        """Every file id in the pod the caller may read, in one statement.

        This replaces a loop that loaded *every* file row in the pod, hydrated
        them into ORM objects and then entities,
        collected the ancestor path of each, re-queried by those paths, and
        then re-derived inheritance in Python. The predicate it re-derived is
        the same ``_file_actions_expr`` CASE used everywhere else, so it was
        being evaluated in SQL and then again, differently, above it.

        ``walk_ancestors`` is the human/workload split, and it is a real
        difference in the rule rather than an optimization. A workload holds no
        ambient access, so ``_file_actions_expr`` — which already resolves the
        grant cascade — is the whole answer: re-deriving inheritance on top of
        it would demand a separate grant on every folder above and cancel the
        cascade it just followed. That regression withheld every file from an
        agent holding a real folder grant. A human, by contrast, may
        read a POD file by role alone, so an unreadable folder above it has to
        hide what is inside.
        """
        actions = _file_actions_expr(ctx)
        stmt = select(DatastoreFile.id).where(
            DatastoreFile.pod_id == pod_id,
            allowed_actions_contains(actions, Permissions.FOLDER_READ),
        )
        if walk_ancestors:
            stmt = stmt.where(~has_unreadable_ancestor(ctx, pod_id))
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def file_visibility_split(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk_ancestors: bool,
    ) -> tuple[set[UUID], set[UUID]]:
        """``(visible, hidden)`` for the whole pod, in one statement.

        Search sends its filter to a *different database* — chunks live in the
        pod's datastore schema, and there is no join back to here — so the ids
        travel as an array either way. Which side to send is then a question of
        length, and it is worth asking: in the observed data most files are
        POD-visible and RESTRICTED is rare, so the hidden side is usually the
        short one and often empty. Returning both costs the same single scan.

        The predicate is the same one ``visible_file_ids`` uses, projected as a
        boolean instead of applied as a filter, so the two cannot drift.
        """
        actions = _file_actions_expr(ctx)
        visible_expr = allowed_actions_contains(actions, Permissions.FOLDER_READ)
        if walk_ancestors:
            visible_expr = and_(visible_expr, ~has_unreadable_ancestor(ctx, pod_id))
        rows = await self.session.execute(
            select(DatastoreFile.id, visible_expr.label("visible")).where(
                DatastoreFile.pod_id == pod_id
            )
        )
        visible: set[UUID] = set()
        hidden: set[UUID] = set()
        for file_id, is_visible in rows.all():
            (visible if is_visible else hidden).add(file_id)
        return visible, hidden

    async def get_tree_items(
        self,
        pod_id: UUID,
        *,
        ctx: Context,
        subtree_root: str,
        files_per_directory: int,
        walk_ancestors: bool,
    ) -> Sequence[DatastoreFileEntity]:
        """Everything a directory tree can display, and nothing else.

        See ``file_tree_sql.tree_statements`` for the shape and why it is two
        statements rather than one read of the pod.
        """
        folders_stmt, files_stmt = tree_statements(
            _file_actions_expr(ctx),
            pod_id,
            ctx,
            subtree_root=subtree_root,
            files_per_directory=files_per_directory,
            walk_ancestors=walk_ancestors,
        )
        folders = await self.session.execute(folders_stmt)
        items = [instance.to_entity() for instance in folders.scalars().all()]
        files = await self.session.execute(files_stmt)
        items.extend(instance.to_entity() for instance in files.scalars().all())
        return items

    async def rewrite_descendant_paths(
        self,
        pod_id: UUID,
        *,
        previous_prefix: str,
        new_prefix: str,
        expected: int,
    ) -> int:
        """Repoint every path under a renamed folder; see `repoint_descendants`.

        This was a `SELECT` plus an `UPDATE` per descendant -- `update()` reads
        the row before writing it -- so renaming a folder of five hundred files
        issued a thousand statements inside the request transaction.

        ``expected`` is how many descendants the copy plan was built from, and
        the count this returns has to match it. The plan is taken before the
        storage phase and the bytes are copied from it, so a file that appeared
        under the folder in between would be repointed here with nothing at its
        new key -- a row naming an object nobody wrote. A file that vanished is
        the same disagreement facing the other way.

        Refusing is the answer rather than repairing: the transaction rolls back,
        the rename is reported as failed, and the caller retries against a tree
        that has stopped moving. The old per-row loop re-read the descendants
        after the copies and repointed the late file just the same, silently.
        """
        result = await self.session.execute(
            repoint_descendants(
                pod_id, previous_prefix=previous_prefix, new_prefix=new_prefix
            )
        )
        moved = int(result.rowcount or 0)
        if moved != expected:
            raise DatastoreConflictError(
                f"{new_prefix} changed while it was being renamed: "
                f"{expected} entries were staged and {moved} were found. "
                "Nothing was moved; try again."
            )
        return moved


def _file_payload_unset(entity: DatastoreFileEntity) -> dict:
    data = entity.model_dump(exclude_unset=True)
    data["kind"] = entity.kind.value
    data["status"] = entity.status.value
    data["file_metadata"] = data.pop("metadata", None)
    return data

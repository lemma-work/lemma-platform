from __future__ import annotations

from typing import Iterable, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import (
    and_,
    delete,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.sql import ColumnElement

from app.core.authorization.authz_epoch import bump_after_commit
from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.sql_actions import allowed_actions_expr
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreRecordNotFoundError,
)
from app.core.infrastructure.db.transaction_locks import (
    mark_transaction_scoped_lock,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.search_scope import (
    SearchablePartition,
    SearchScopeTimedOut,
)
from app.modules.datastore.domain.ports import DatastoreFileRepositoryPort
from app.modules.datastore.infrastructure.models import DatastoreFile
from app.modules.datastore.infrastructure.repositories.file_tree_sql import (
    tree_statements,
)
from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    has_ancestor_where,
)
from app.modules.datastore.infrastructure.repositories.file_read_rule import (
    resolve_blocked_folders,
    resolve_read_rule,
    searchable_side_statement,
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
    stragglers_under,
)
from app.modules.datastore.infrastructure.repositories.file_status_counts import (
    DatastoreFileStatusCountsMixin,
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


#: Postgres' SQLSTATE for a statement cancelled by ``statement_timeout``.
_QUERY_CANCELED = "57014"


def _file_payload(entity: DatastoreFileEntity) -> dict:
    payload = entity.model_dump(exclude={"allowed_actions"})
    payload["kind"] = entity.kind.value
    payload["status"] = entity.status.value
    payload["file_metadata"] = payload.pop("metadata", None)
    return payload


class DatastoreFileRepository(
    DatastoreFileListingMixin,
    DatastoreFileProcessingStateMixin,
    DatastoreFileStatusCountsMixin,
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
        await self._enqueue_requested_processing(entity, instance)
        bump_after_commit(self.session, instance.pod_id, files=True)
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

        # Who may read what, and what search can reach, change with these;
        # anything cached from the old answer is retired after commit.
        reach_changed = (
            instance.visibility != entity.visibility
            or instance.path != entity.path
            or instance.search_enabled != entity.search_enabled
        )
        for key, value in _file_payload_unset(entity).items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)
        if reach_changed:
            bump_after_commit(self.session, instance.pod_id, files=True)

        await self.session.flush()
        self._collect_events(entity)
        await self._enqueue_requested_processing(entity, instance)
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
        bump_after_commit(self.session, instance.pod_id, files=True)
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
        bump_after_commit(self.session, entities[0].pod_id, files=True)
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
        # Filter on the resolved read rule; the full action projection is only
        # computed for the rows the page returns.
        readable = await self.readable_where(
            pod_id=pod_id, ctx=ctx, walk_ancestors=False
        )
        stmt = select(DatastoreFile, _file_actions_expr(ctx)).where(
            DatastoreFile.pod_id == pod_id,
            DatastoreFile.path.like(direct, escape="!"),
            ~DatastoreFile.path.like(nested, escape="!"),
            readable,
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

    async def list_readable_anywhere(
        self, pod_id: UUID, ctx: Context, *, limit: int
    ) -> Sequence[DatastoreFileEntity]:
        """Every file and folder in the pod ``ctx`` may read, at any depth, by path.

        The listing above is one directory at a time, because a person walks a
        tree. This is for asking what a narrow reader can reach at all -- an
        outsider's run can open a Public file wherever it sits.
        """
        readable = await self.readable_where(
            pod_id=pod_id, ctx=ctx, walk_ancestors=False
        )
        result = await self.session.execute(
            select(DatastoreFile, _file_actions_expr(ctx))
            .where(DatastoreFile.pod_id == pod_id, readable)
            .order_by(DatastoreFile.path)
            .limit(limit)
        )
        return [
            self._with_allowed_actions(item.to_entity(), allowed)
            for item, allowed in result.all()
        ]

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
        """The row-alone rule over a short list, with no ancestor walk.

        ``get_visible_file_ids_for_items`` owns the walk for this shape: it
        already holds the rows, so it builds the ancestor context once and
        climbs it in Python. Search takes ``walk_ancestors=True`` through
        ``visible_file_ids`` instead, which does the climb in SQL.
        """
        return await self.visible_file_ids(
            pod_id=pod_id,
            ctx=ctx,
            walk_ancestors=False,
            among=file_ids,
        )

    async def visible_file_ids(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk_ancestors: bool,
        among: Iterable[UUID] | None = None,
        limit: int | None = None,
    ) -> set[UUID]:
        """The file ids in the pod the caller may read.

        The read rule is resolved first -- one read of the caller's grants and,
        for a full-pod walk, one of the unreadable rows that have rows beneath
        them -- and then applied as a plain predicate (``readable_where``), so
        the statement over the pod runs no subquery per row. The answer is the
        one ``_file_actions_expr`` gives row by row, ancestor walk included.

        ``walk_ancestors`` is the human/workload split, and it is a real
        difference in the rule rather than an optimization. A workload holds no
        ambient access, so the row's own grant cascade is the whole answer:
        re-deriving inheritance on top of it would demand a separate grant on
        every folder above and cancel the cascade it just followed. That
        regression withheld every file from an agent holding a real folder
        grant. A human, by contrast, may read a POD file by role alone, so an
        unreadable folder above it has to hide what is inside.

        ``among`` narrows the question to a known list -- the shape search uses
        to authorize a candidate pool -- and costs a primary-key lookup per id
        rather than a pass over the pod. Its walk probes each candidate's
        ancestors rather than reading the blocked rows of the whole pod.

        ``limit`` stops the statement early; a truncated result is a verdict
        ("more than this"), never a result, which is why there is no ORDER BY.
        """
        if among is not None:
            among = list(among)
            if not among:
                return set()
        readable = await self.readable_where(
            pod_id=pod_id,
            ctx=ctx,
            walk_ancestors=walk_ancestors,
            per_row_walk=among is not None,
        )
        stmt = select(DatastoreFile.id).where(DatastoreFile.pod_id == pod_id, readable)
        if among is not None:
            stmt = stmt.where(DatastoreFile.id.in_(among))
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def readable_where(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk_ancestors: bool,
        per_row_walk: bool = False,
    ) -> ColumnElement[bool]:
        """The read predicate over ``DatastoreFile`` for *ctx*, authority first.

        The caller's grants are read once (and, for a walk, the unreadable rows
        that have rows beneath them), so what is left per row is a CASE over
        its own columns. See ``file_read_rule`` for why, and for the rule this
        must agree with.

        ``per_row_walk`` probes each row's ancestors instead of reading the
        pod's blocked folders up front -- cheaper when the outer query is
        already bounded to a short list of ids.
        """
        rule = await resolve_read_rule(self.session, pod_id=pod_id, ctx=ctx)
        readable = rule.readable()
        if not walk_ancestors or rule.reads_everything:
            return readable
        if per_row_walk:
            return and_(
                readable,
                ~has_ancestor_where(pod_id, lambda ancestor: ~rule.readable(ancestor)),
            )
        blocked = await resolve_blocked_folders(self.session, pod_id=pod_id, rule=rule)
        if blocked.empty:
            return readable
        return and_(readable, ~blocked.hides(DatastoreFile.path))

    async def searchable_partition(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk_ancestors: bool,
        timeout_ms: int,
        limit: int | None = None,
        readable_only: bool = False,
    ) -> SearchablePartition:
        """The pod's searchable files, split into readable and hidden.

        Each side is read up to *limit* rows (all of it when None), so a pod
        with millions of searchable files costs the split ``2 * limit`` ids,
        not millions; *readable_only* skips the hidden side, for the caller
        that needs the complete readable set and nothing else.

        Bounded in time as well. The statements run inside a
        savepoint under ``statement_timeout``; the savepoint is rolled back
        afterwards either way, which is what returns the timeout to the
        session's own and leaves the transaction usable after a cancel. A
        cancel raises ``SearchScopeTimedOut`` so search can fall back to
        authorizing its results instead of failing.
        """
        savepoint = await self.session.begin_nested()
        try:
            await self.session.execute(
                text("SELECT set_config('statement_timeout', :ms, true)"),
                {"ms": str(int(timeout_ms))},
            )
            readable = await self.readable_where(
                pod_id=pod_id, ctx=ctx, walk_ancestors=walk_ancestors
            )
            readable_ids = set(
                (
                    await self.session.execute(
                        searchable_side_statement(
                            pod_id, readable, readable_side=True, limit=limit
                        )
                    )
                ).scalars()
            )
            hidden_ids: set[UUID] = (
                set()
                if readable_only
                else set(
                    (
                        await self.session.execute(
                            searchable_side_statement(
                                pod_id, readable, readable_side=False, limit=limit
                            )
                        )
                    ).scalars()
                )
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == _QUERY_CANCELED:
                raise SearchScopeTimedOut(
                    f"search scope exceeded {timeout_ms}ms"
                ) from exc
            raise
        finally:
            await savepoint.rollback()
        return SearchablePartition(
            readable=frozenset(readable_ids), hidden=frozenset(hidden_ids)
        )

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
            await self.readable_where(
                pod_id=pod_id, ctx=ctx, walk_ancestors=walk_ancestors
            ),
            pod_id,
            subtree_root=subtree_root,
            files_per_directory=files_per_directory,
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
        planned: Sequence[tuple[UUID, str]],
    ) -> int:
        """Repoint a renamed folder's descendants; see `repoint_descendants`.

        This was a `SELECT` plus an `UPDATE` per descendant -- `update()` reads
        the row before writing it -- so renaming a folder of five hundred files
        issued a thousand statements inside the request transaction.

        ``planned`` is the ``(id, path)`` of every descendant the copy plan was
        built from, and both halves are load-bearing. The bytes were copied
        from those paths, so a row that no longer holds the path it was copied
        from must not be repointed: its new path would name an object nobody
        wrote. Pairing the id with the path is what refuses that, and it is
        what a row *count* could not -- renaming one child inside the folder
        while the copy ran left the count unchanged and the fence silent.

        Two disagreements are possible and both are refused. A planned row that
        moved, vanished, or was renamed does not match its pair, so fewer rows
        move than were staged. A row that arrived under the old path after the
        plan was taken is in no pair at all, so it does not move -- and would
        be stranded under a folder that no longer exists, which is why
        ``stragglers_under`` looks for it rather than trusting the count.

        Refusing is the answer rather than repairing: the transaction rolls
        back, the rename is reported as failed, and the caller retries against
        a tree that has stopped moving. The old per-row loop re-read the
        descendants after the copies and repointed the late file just the same,
        silently.
        """
        expected = len(planned)
        moved = 0
        if planned:
            result = await self.session.execute(
                repoint_descendants(
                    pod_id,
                    previous_prefix=previous_prefix,
                    new_prefix=new_prefix,
                    planned=planned,
                )
            )
            moved = int(result.rowcount or 0)
        if moved != expected:
            raise DatastoreConflictError(
                f"{new_prefix} changed while it was being renamed: "
                f"{expected} entries were staged and {moved} still held the "
                "path their contents were copied from. Nothing was moved; "
                "try again."
            )
        straggler = await self.session.execute(
            stragglers_under(pod_id, previous_prefix)
        )
        if straggler.scalars().first() is not None:
            raise DatastoreConflictError(
                f"{previous_prefix} gained an entry while it was being "
                "renamed, which has no contents at the new path. Nothing was "
                "moved; try again."
            )
        return moved


def _file_payload_unset(entity: DatastoreFileEntity) -> dict:
    data = entity.model_dump(exclude_unset=True)
    data["kind"] = entity.kind.value
    data["status"] = entity.status.value
    data["file_metadata"] = data.pop("metadata", None)
    return data

"""Revision-history reads and writes for a function.

Split out of ``repositories.py`` because that module crossed the architecture
ratchet's per-file ceiling once both this branch and main had grown it. The seam
is the same one the services already draw: that file owns a function's identity
and its runs, this owns the index of the builds it has had.

A mixin rather than a separate repository so callers keep one object -- how the
file divides is not a distinction the service layer should have to know about.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.sql_text import starts_with

from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRevisionEntity,
    FunctionRunStatus,
    FunctionStatus,
)
from app.modules.function.infrastructure.models import (
    FunctionModel,
    FunctionRevisionModel,
    FunctionRunModel,
)


def revision_prefix_statement(function_id: UUID, prefix: str):
    """Revisions whose hash starts with *prefix*, one row per distinct hash.

    The stored hash carries its ``sha256:`` algorithm prefix -- the entity's own
    pattern requires it -- while a typed ref does not, so the literal goes back
    on here. That keeps the match anchored, which is what lets
    ``ix_function_revision_function_hash_prefix`` serve it;
    ``uq_function_revision_active_hash`` looks like it would do instead, but it
    is partial on ``pruned_at IS NULL`` and ref resolution has to see pruned
    rows so it can tell "removed by retention" from "never existed".

    Ambiguity counts **distinct hashes, not rows**: republishing identical code
    writes another revision carrying the same hash and is not ambiguous, so
    ``DISTINCT ON`` collapses each hash to its best row -- live before pruned,
    then newest -- before the limit applies.

    A module-level builder so the statement a test explains is the statement the
    repository runs; a copy in the test would certify itself.
    """
    return (
        select(FunctionRevisionModel)
        .where(
            FunctionRevisionModel.function_id == function_id,
            starts_with(FunctionRevisionModel.revision_hash, f"sha256:{prefix}"),
        )
        .distinct(FunctionRevisionModel.revision_hash)
        # DISTINCT ON keeps the first row of each group, so its column has to
        # lead the ordering and the tiebreak follows. `revision_number` is NOT
        # NULL here, so DESC needs no NULLS clause to match the Python it
        # replaces.
        .order_by(
            FunctionRevisionModel.revision_hash,
            FunctionRevisionModel.pruned_at.is_not(None),
            FunctionRevisionModel.revision_number.desc(),
        )
        .limit(2)
    )


class FunctionRevisionRepositoryMixin:
    """Persist revision history within the host repository transaction."""

    session: AsyncSession

    async def get_for_update(self, function_id: UUID) -> FunctionEntity | None:
        model = (
            await self.session.execute(
                select(FunctionModel)
                .where(FunctionModel.id == function_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return model.to_entity() if model else None

    async def mark_revisions_purged(self, revision_ids: tuple[UUID, ...]) -> None:
        await self.session.execute(
            update(FunctionRevisionModel)
            .where(
                FunctionRevisionModel.id.in_(revision_ids),
                FunctionRevisionModel.pruned_at.is_not(None),
            )
            .values(purged_at=datetime.now(timezone.utc))
        )

    async def record_revision(
        self, entity: FunctionRevisionEntity
    ) -> FunctionRevisionEntity:
        """Reuse retained code by hash; expired code gets a new row and immutable generation."""
        values = entity.model_dump(
            exclude={
                "id",
                "created_at",
                "code",
                "revision_number",
                "pruned_at",
                "purged_at",
            },
        )
        # Serializes the max+1 below against a concurrent save of DIFFERENT code,
        # which would otherwise compute the same number and violate
        # `uq_function_revision_number`. The callers happen to hold this lock
        # already via the `UPDATE functions` that precedes them in the same unit
        # of work; taking it here stops the numbering depending on an ordering
        # two layers up that a refactor could quietly remove.
        await self.session.execute(
            select(FunctionModel.id)
            .where(FunctionModel.id == entity.function_id)
            .with_for_update()
        )
        statement = (
            insert(FunctionRevisionModel)
            .values(
                id=uuid7(),
                created_at=datetime.now(timezone.utc),
                revision_number=select(
                    func.coalesce(func.max(FunctionRevisionModel.revision_number), 0)
                    + 1
                )
                .where(FunctionRevisionModel.function_id == entity.function_id)
                .scalar_subquery(),
                **values,
            )
            .on_conflict_do_update(
                index_elements=[
                    FunctionRevisionModel.function_id,
                    FunctionRevisionModel.revision_hash,
                ],
                index_where=FunctionRevisionModel.pruned_at.is_(None),
                set_={"revision_hash": entity.revision_hash},
            )
            .returning(FunctionRevisionModel)
        )
        return (await self.session.execute(statement)).scalar_one().to_entity()

    async def get_revision_by_hash(
        self, function_id: UUID, revision_hash: str
    ) -> FunctionRevisionEntity | None:
        statement = select(FunctionRevisionModel).where(
            FunctionRevisionModel.function_id == function_id,
            FunctionRevisionModel.revision_hash == revision_hash,
            FunctionRevisionModel.pruned_at.is_(None),
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

    async def get_revision_by_number(
        self, function_id: UUID, revision_number: int
    ) -> FunctionRevisionEntity | None:
        statement = select(FunctionRevisionModel).where(
            FunctionRevisionModel.function_id == function_id,
            FunctionRevisionModel.revision_number == revision_number,
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

    async def find_revisions_by_hash_prefix(
        self, function_id: UUID, prefix: str
    ) -> list[FunctionRevisionEntity]:
        """The best revision under each distinct hash a prefix names, at most two.

        Callers want one revision and a yes/no on ambiguity, and two rows answer
        both. This replaces reading a function's entire revision history to run
        ``startswith`` over it in Python -- a read that grew with the function's
        lifetime build count to answer a single ref, on a table retention stamps
        rather than empties.
        """
        if not prefix:
            return []
        result = await self.session.execute(
            revision_prefix_statement(function_id, prefix)
        )
        return [model.to_entity() for model in result.scalars().all()]

    async def list_revisions(self, function_id: UUID) -> list[FunctionRevisionEntity]:
        statement = (
            select(FunctionRevisionModel)
            .where(FunctionRevisionModel.function_id == function_id)
            .order_by(
                FunctionRevisionModel.revision_number.desc(),
            )
        )
        result = await self.session.execute(statement)
        return [model.to_entity() for model in result.scalars().all()]

    async def page_revisions(
        self, function_id: UUID, *, limit: int, cursor: UUID | None
    ) -> tuple[list[FunctionRevisionEntity], UUID | None]:
        """One page of a function's history, newest first, plus the next cursor.

        Keyset on the id rather than on `revision_number`, so the page token is
        the bare UUID the house contract uses everywhere else; these rows key on
        uuid7, so id order is creation order and therefore revision order.
        """
        statement = select(FunctionRevisionModel).where(
            FunctionRevisionModel.function_id == function_id
        )
        if cursor is not None:
            statement = statement.where(FunctionRevisionModel.id < cursor)
        rows = list(
            (
                await self.session.execute(
                    statement.order_by(FunctionRevisionModel.id.desc()).limit(limit + 1)
                )
            ).scalars()
        )
        next_cursor = rows[limit - 1].id if len(rows) > limit else None
        return [row.to_entity() for row in rows[:limit]], next_cursor

    async def list_unpurged_revisions(
        self, function_id: UUID
    ) -> list[FunctionRevisionEntity]:
        """The revisions retention can still act on, newest first.

        A purged revision's bytes are already gone: it is not a candidate, it
        is not an unfinished deletion, and because pruning only ever takes from
        the old end it cannot change where any surviving revision ranks. It is
        a tombstone kept so the history stays legible, and retention reading it
        achieved nothing.

        Which matters because retention runs after every save and these rows
        are never deleted -- so the plan's input grew with the function's whole
        lifetime while the set it can choose from stays at `max_keep`. This is
        that bound made real rather than assumed.
        """
        statement = (
            select(FunctionRevisionModel)
            .where(
                FunctionRevisionModel.function_id == function_id,
                FunctionRevisionModel.purged_at.is_(None),
            )
            .order_by(FunctionRevisionModel.revision_number.desc())
        )
        result = await self.session.execute(statement)
        return [model.to_entity() for model in result.scalars().all()]

    async def revision_hashes_with_runs_in_flight(self, function_id: UUID) -> set[str]:
        """Revision hashes a PENDING or RUNNING run is pinned to.

        A run resolves its artifact from its OWN hash at execution time, so
        deleting the artifact under a dispatched run makes it fail with a
        digest error instead of running. Retention skips these.
        """
        statement = select(FunctionRunModel.revision_hash).where(
            FunctionRunModel.function_id == function_id,
            FunctionRunModel.revision_hash.is_not(None),
            FunctionRunModel.status.in_(
                [FunctionRunStatus.PENDING, FunctionRunStatus.RUNNING]
            ),
        )
        result = await self.session.execute(statement)
        return {row for row in result.scalars().all() if row}

    async def mark_revisions_pruned(self, revision_ids: list[UUID]) -> None:
        if not revision_ids:
            return
        await self.session.execute(
            update(FunctionRevisionModel)
            .where(
                FunctionRevisionModel.id.in_(revision_ids),
                FunctionRevisionModel.pruned_at.is_(None),
            )
            .values(pruned_at=datetime.now(timezone.utc))
        )

    async def activate_revision(
        self, function_id: UUID, revision: FunctionRevisionEntity
    ) -> FunctionEntity | None:
        """Make ``revision`` the function's live one, contract included.

        The schemas move with the hash: they live on the function row, and every
        agent and workflow bound to this function reads them, so leaving the
        newest schemas next to older code would advertise a contract the code
        does not implement.
        """
        statement = (
            update(FunctionModel)
            .where(FunctionModel.id == function_id)
            .values(
                revision_hash=revision.revision_hash,
                code_path=revision.code_path,
                input_schema=revision.input_schema,
                output_schema=revision.output_schema,
                config_schema=revision.config_schema,
                status=FunctionStatus.READY,
            )
            .returning(FunctionModel)
        )
        model = (await self.session.execute(statement)).scalar_one_or_none()
        return model.to_entity() if model else None

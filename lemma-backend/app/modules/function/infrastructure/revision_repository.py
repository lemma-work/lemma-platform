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

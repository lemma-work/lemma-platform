"""Persistence adapter for the pod-import aggregate.

Implements the ``ImportRepository`` port over SQLAlchemy. ``save`` upserts the
single row, copying mutable state onto an attached model on update so each
per-step checkpoint is durable.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.infrastructure.models import PodImportModel


class PodImportRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    async def save(self, entity: PodImportEntity) -> None:
        existing = await self.session.get(PodImportModel, entity.id)
        if existing is None:
            self.session.add(PodImportModel.from_entity(entity))
        else:
            existing.apply_entity(entity)
        # A real commit (not just flush) — every checkpoint call site in
        # ImportService.apply() needs to be durable and visible to a
        # concurrent GET the instant it happens, or a polling frontend can
        # never see live progress during the (single, blocking) apply
        # request. Safe: this repository is only ever used for these
        # checkpoints, and async_session_maker has expire_on_commit=False,
        # so objects loaded earlier in the same apply loop stay usable.
        await self.session.commit()

    async def get(self, import_id: UUID) -> PodImportEntity | None:
        model = await self.session.get(PodImportModel, import_id)
        return model.to_entity() if model else None

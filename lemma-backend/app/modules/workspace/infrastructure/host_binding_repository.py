"""Reading and writing a host sandbox's binding to its Agent Host."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.host_execution import HostBinding
from app.modules.workspace.infrastructure.models import SandboxHostBindingModel


class HostBindingRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.session = uow.session

    async def get(self, sandbox_id: UUID) -> HostBinding | None:
        row = (
            await self.session.execute(
                select(SandboxHostBindingModel).where(
                    SandboxHostBindingModel.sandbox_id == sandbox_id
                )
            )
        ).scalar_one_or_none()
        return row.to_entity() if row is not None else None

    async def bind(self, binding: HostBinding) -> None:
        """Record which host a run chose. ``root`` is kept until the host says.

        The host id is overwritten: a new run choosing a newly paired Mac must
        reach it. A run in flight is unaffected, because it was chosen, and
        this row written, before its first operation.
        """
        now = datetime.now(timezone.utc)
        values = {
            "host_id": binding.host_id,
            "owner_id": binding.owner_id,
            "conversation_id": binding.conversation_id,
            "slug": binding.slug,
            "day": binding.day,
            "root_hint": binding.root_hint,
            "updated_at": now,
        }
        statement = pg_insert(SandboxHostBindingModel).values(
            id=uuid7(), sandbox_id=binding.sandbox_id, created_at=now, **values
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[SandboxHostBindingModel.sandbox_id], set_=values
            )
        )

    async def set_root(self, sandbox_id: UUID, root: str) -> None:
        await self.session.execute(
            update(SandboxHostBindingModel)
            .where(SandboxHostBindingModel.sandbox_id == sandbox_id)
            .values(root=root, updated_at=datetime.now(timezone.utc))
        )

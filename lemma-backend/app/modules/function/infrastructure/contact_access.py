"""Whether a function is open to contacts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.infrastructure.models import FunctionModel


async def set_contacts_invoke(
    uow: SqlAlchemyUnitOfWork, *, function_id: UUID, contacts_invoke: bool
) -> None:
    await uow.session.execute(
        update(FunctionModel)
        .where(FunctionModel.id == function_id)
        .values(contacts_invoke=contacts_invoke)
    )

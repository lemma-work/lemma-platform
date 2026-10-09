"""Whether a function is open to contacts."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.function.domain.entities import FunctionEntity
from app.modules.function.infrastructure.models import FunctionModel

logger = get_logger(__name__)


async def set_contacts_invoke(
    uow: SqlAlchemyUnitOfWork,
    *,
    function: FunctionEntity,
    contacts_invoke: bool,
    changed_by: UUID | None,
) -> None:
    assert function.id is not None
    await uow.session.execute(
        update(FunctionModel)
        .where(FunctionModel.id == function.id)
        .values(contacts_invoke=contacts_invoke)
    )
    # The audit record of who let strangers start this function, or stopped
    # them. ``user_id`` is the member's id, never their address.
    logger.info(
        "function.contact_access.changed.observed",
        function_id=str(function.id),
        pod_id=str(function.pod_id),
        contacts_invoke=contacts_invoke,
        user_id=str(changed_by) if changed_by else None,
    )

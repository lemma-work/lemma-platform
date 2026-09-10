"""Attach the run outcome after draining its monetary receipts."""

from uuid import UUID

from sqlalchemy import update

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.services.metering_scope import current_metering_scope


async def finalize_metered_run(
    agent_run_id: UUID, status: str, *, factory: UnitOfWorkFactory
) -> None:
    scope = current_metering_scope()
    if scope is not None and scope.context.agent_run_id == agent_run_id:
        # The terminal event can arrive before the execution context exits.
        # Retry unfinished writes before labeling their final outcome.
        await scope.close()
    async with factory() as uow:
        await uow.session.execute(
            update(UsageRecord)
            .where(
                UsageRecord.agent_run_id == agent_run_id,
                UsageRecord.request_id.is_not(None),
            )
            .values(status=status)
        )

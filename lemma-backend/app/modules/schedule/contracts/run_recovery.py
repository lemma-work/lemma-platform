"""The ledger sweep, as the cron that runs it sees it.

One operation and the count it returns, not `ScheduleRunRecoveryService`. The
caller is a worker cron with a transaction in hand; what it needs is "repair
what the event path missed, and tell me whether anything was wrong", and a
service class would have made the cron's registration depend on how this module
assembles its own collaborators.

A submodule for the same reason as `dispatch.py` beside it: this reaches the
service layer, and `contracts/__init__` is imported by anything that wants any
contract at all.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.services.run_recovery_service import (
    ScheduleRunRecoveryResult,
    ScheduleRunRecoveryService,
)


async def recover_schedule_runs(
    uow: SqlAlchemyUnitOfWork, *, limit: int | None = None
) -> ScheduleRunRecoveryResult:
    """Repair one batch of runs whose outcome nothing recorded."""
    service = ScheduleRunRecoveryService(uow)
    if limit is None:
        return await service.recover()
    return await service.recover(limit=limit)


__all__ = ["ScheduleRunRecoveryResult", "recover_schedule_runs"]

"""`DatastoreSchedulePolicy`, answered against the pod's datastore.

Who may point a schedule at a table, and whose runs against it they may read.
Both questions are schedule's -- the port is schedule's, and so is the error
raised when the table named in a schedule does not exist -- but answering them
needs one fact from datastore, which arrives through
`datastore/contracts/schedule_tables.py`.

This was `app/composition/schedule_datastore_policy.py`, where it held a
`DatastoreTableRepository` and read the table row's `id` and `enable_rls`
itself. The policy is not a composition concern: nothing about it varies by
deployment, and putting it in the root meant `schedule.services.schedule_service`
imported the application root in order to construct its own default.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.contracts.schedule_tables import schedule_table_target
from app.modules.schedule.domain.errors import ScheduleValidationError


class DatastoreTableSchedulePolicy:
    """The datastore-backed answers a schedule's table checks need."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def require_table_update(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> None:
        target = await schedule_table_target(
            self._uow, pod_id=pod_id, table_name=table_name, ctx=ctx
        )
        if target is None:
            raise ScheduleValidationError(
                f"Datastore table '{table_name}' was not found in this pod."
            )
        await ctx.require(Permissions.DATASTORE_TABLE_UPDATE, target.ref)

    async def can_view_all_runs(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> bool:
        target = await schedule_table_target(
            self._uow, pod_id=pod_id, table_name=table_name, ctx=ctx
        )
        if target is None:
            return False
        if not target.enable_rls:
            return True
        return await ctx.can(Permissions.DATASTORE_TABLE_DELETE, target.ref)

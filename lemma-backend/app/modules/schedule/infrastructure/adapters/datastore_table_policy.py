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

    async def require_table_watch(
        self, *, pod_id: UUID, table_name: str, owner_readable_rows: bool, ctx: Context
    ) -> None:
        """May the caller point a trigger at this table.

        A trigger that sees every change takes ``datastore.table.update``: on
        a table where each person has their own rows it reaches rows its
        author cannot read. One that sees only the rows its owner can read
        (``rows: OWNER_READABLE``) takes ``datastore.record.read`` -- reading
        those rows is all it does, and it runs as nobody but its owner.
        """
        target = await schedule_table_target(
            self._uow, pod_id=pod_id, table_name=table_name, ctx=ctx
        )
        if target is None:
            raise ScheduleValidationError(
                f"Datastore table '{table_name}' was not found in this pod."
            )
        permission = (
            Permissions.DATASTORE_RECORD_READ
            if owner_readable_rows
            else Permissions.DATASTORE_TABLE_UPDATE
        )
        await ctx.require(permission, target.ref)

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

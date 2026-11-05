"""Keeping a DATASTORE trigger's change-log subscriber in step with its row.

Called by every write that can change what a trigger watches -- create, an
edit of its config or active flag, a pause, a delete -- in that write's
transaction. The ``changes.sync_subscribers`` item it writes reads the
schedule back after commit and creates, updates or drops the subscriber, so
the change log never needs to be told *what* changed, only that something may
have.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.contracts.changes import (
    request_subscriber_sync,
    subscriber_key,
)
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.infrastructure.models.schedule import Schedule

PREFIX = "schedule"


class TriggerRow(Protocol):
    """A schedule row, or a ``RETURNING pod_id, schedule_type`` row."""

    @property
    def pod_id(self) -> UUID | None: ...

    @property
    def schedule_type(self) -> ScheduleType | str: ...


#: An edit of anything else leaves what a trigger watches as it was.
_WATCHED_FIELDS = frozenset({"config", "is_active"})


async def sync_trigger_subscription(
    uow: SqlAlchemyUnitOfWork,
    schedule_id: UUID,
    row: TriggerRow,
    changed: Collection[str] | None = None,
) -> None:
    """``changed``: the fields an edit wrote; ``None`` for a create, pause or delete."""
    if changed is not None and not _WATCHED_FIELDS.intersection(changed):
        return
    if row.pod_id is None or ScheduleType(row.schedule_type) != ScheduleType.DATASTORE:
        return
    await request_subscriber_sync(uow, row.pod_id, subscriber_key(PREFIX, schedule_id))


_RESYNC_PAGE = 500


async def resync_pod_triggers(uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> int:
    """Ask a sync for every active DATASTORE trigger of ``pod_id``; returns how many.

    For a change to the pod's trigger defaults, which every trigger that
    leaves a write mode unset inherits.
    """
    after: UUID | None = None
    count = 0
    while True:
        query = select(Schedule.id).where(
            Schedule.pod_id == pod_id,
            Schedule.schedule_type == ScheduleType.DATASTORE,
            Schedule.is_active.is_(True),
        )
        if after is not None:
            query = query.where(Schedule.id > after)
        ids = list(
            await uow.session.scalars(query.order_by(Schedule.id).limit(_RESYNC_PAGE))
        )
        for schedule_id in ids:
            await request_subscriber_sync(
                uow, pod_id, subscriber_key(PREFIX, schedule_id)
            )
        count += len(ids)
        if len(ids) < _RESYNC_PAGE:
            return count
        after = ids[-1]


__all__ = ["PREFIX", "resync_pod_triggers", "sync_trigger_subscription"]

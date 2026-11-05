"""The record change log, as other modules subscribe to it.

A module with durable subscribers (DATASTORE triggers, MCP event
subscriptions) registers one :class:`DurableSubscriber` for its prefix at
import, and writes :func:`request_subscriber_sync` in the same transaction
that creates, edits, pauses or deletes one. The change log then keeps the
subscriber's row, wakes it when its table changes, and hands it pages of
changes in ``(xid, seq)`` order; see ``app.modules.datastore.changes``.

A submodule rather than ``contracts/__init__``: it imports the change-log
machinery, which a module importing any other datastore contract should not
pay for.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkWriter
from app.modules.datastore.changes.cursor import ChangePosition
from app.modules.datastore.changes.reader import Change, ChangeOp, JsonRow
from app.modules.datastore.changes.subscribers import (
    DeliverOutcome,
    DurableSubscriber,
    LapsedSpan,
    SubscriberSpec,
    register_durable_subscriber,
    subscriber_key,
)
from app.modules.datastore.changes.work import request_sync
from app.modules.datastore.contracts.write_intent import WriteIntent


async def request_subscriber_sync(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, subscriber: str
) -> None:
    """Bring ``subscriber``'s row in line with its module's state, after commit.

    A new subscriber starts with the changes written from this transaction's
    start, by the database's clock (``now()``), which is earlier than any
    clock reading this process could take once the transaction is open.
    """
    since = await uow.session.scalar(select(func.now()))
    await request_sync(WorkWriter.of(uow), pod_id, subscriber, since=since)


__all__ = [
    "Change",
    "ChangeOp",
    "ChangePosition",
    "DeliverOutcome",
    "DurableSubscriber",
    "JsonRow",
    "LapsedSpan",
    "SubscriberSpec",
    "WriteIntent",
    "register_durable_subscriber",
    "request_subscriber_sync",
    "subscriber_key",
]

"""A unit of work inside a transaction the work runtime commits.

A FAST handler's effect commits with its item's deletion, and a JOB's outcome
with its fenced completion: the runtime owns that commit. The agent services a
handler reuses -- appending a pause's tool return, starting the resume run --
were written for a request, where they commit as they go. Handed this instead,
their ``commit()`` only flushes, and everything they defer (outbox events,
after-commit callbacks) is carried by the runtime's own unit of work, so it
happens when the runtime commits and not before.

The session is also marked as holding transaction-scoped state: the item's row
lock dies at commit, and a "release the connection" helper that committed early
would hand the item to another worker mid-effect.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from app.core.domain.events import DomainEvent


class TransactionOwnedByRuntime(RuntimeError):
    """Something tried to roll back a transaction only the work runtime may end."""


class RuntimeOwnedUnitOfWork(SqlAlchemyUnitOfWork):
    """``outer``'s transaction, for code that expects to commit it."""

    def __init__(self, outer: SqlAlchemyUnitOfWork) -> None:
        super().__init__(outer.session)
        self._outer = outer
        mark_transaction_scoped_lock(outer.session)

    def collect_events(self, events: Sequence["DomainEvent"]) -> None:
        self._outer.collect_events(events)

    def after_commit(self, callback: Callable[[], Awaitable[object]]) -> None:
        self._outer.after_commit(callback)

    def has_pending_events(self) -> bool:
        return self._outer.has_pending_events()

    async def commit(self) -> None:
        # Writes reach the database now, so later reads in this transaction see
        # them; the commit itself is the runtime's.
        await self.session.flush()

    async def rollback(self) -> None:
        # Raised rather than ignored: code that rolls back expects its writes
        # gone, and silently keeping them would commit them with the item.
        raise TransactionOwnedByRuntime(
            "this transaction is committed or rolled back by the work runtime; "
            "raise instead of rolling back"
        )


__all__ = ["RuntimeOwnedUnitOfWork", "TransactionOwnedByRuntime"]

"""What a fast handler and a dead hook are given.

Both run inside a transaction the runtime owns and commits: a fast handler's
effect commits with the deletion of its item, and a dead hook's terminal domain
state commits with the item's move to ``dead``. Neither may commit on its own.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.types import ClaimedItem
from app.core.infrastructure.work.writer import WorkWriter


@dataclass(frozen=True, slots=True)
class FastContext:
    """A fast handler's transaction, fenced by the item's row lock.

    ``uow`` is the open unit of work; repositories built on ``uow.session``
    write into the same transaction. ``work`` enqueues follow-up items in it.
    No network: anything that leaves the main database is a job.
    """

    uow: SqlAlchemyUnitOfWork
    item: ClaimedItem
    work: WorkWriter


@dataclass(frozen=True, slots=True)
class DeadContext:
    """The transaction that moves an item to ``dead``.

    A kind's ``on_dead`` hook writes the domain's terminal state here -- a file
    to FAILED_PERMANENT, a run to FAILED with a reason the UI shows -- so no
    item dies without its owner hearing about it.
    """

    uow: SqlAlchemyUnitOfWork
    item: ClaimedItem
    reason: str
    last_error: str | None
    work: WorkWriter

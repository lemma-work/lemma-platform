"""Every transition of a claimed item, each fenced by its lease.

An item a process no longer owns -- its lease expired and someone else claimed
it, or it is gone -- matches zero rows here, so a zombie can never complete,
fail or release work that is not its own. Every path back to ``queued`` goes
through ``requeue``, which is also where a dedup key is reconciled with a twin
queued meanwhile.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from opentelemetry import metrics
from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work.context import DeadContext
from app.core.infrastructure.work.registry import HandlerSpec, WorkRegistry
from app.core.infrastructure.work.retry import AttemptState, Dead, Decision, Requeue
from app.core.infrastructure.work.types import ClaimedItem, WorkClass
from app.core.infrastructure.work.writer import WorkWriter, wake_channel
from app.core.log.log import get_logger

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)
dead_counter = meter.create_counter(
    "lemma.work.dead", description="Work items that reached the dead state."
)

_LOCK_OWNED = text(
    """
    SELECT attempts, lease_losses, first_unavailable_at FROM work_items
     WHERE id = :id AND lease_owner = :owner AND lease_epoch = :epoch
       AND state = 'running'
       FOR UPDATE
    """
)
# The same lock, also setting the handler's statement timeout for the rest of
# its transaction (``set_config(..., true)`` is ``SET LOCAL``): a fast handler
# is a few statements, and this was one round trip of them on every item.
_LOCK_OWNED_WITH_TIMEOUT = text(
    """
    SELECT attempts, lease_losses, first_unavailable_at,
           set_config('statement_timeout', :timeout_ms, true) AS timeout
      FROM work_items
     WHERE id = :id AND lease_owner = :owner AND lease_epoch = :epoch
       AND state = 'running'
       FOR UPDATE
    """
)
# The same question without the lock, for a commit that must not wait on the
# item's row or make anything wait on it (``JobContext.check_lease_at_commit``).
_STILL_OWNED = text(
    """
    SELECT 1 FROM work_items
     WHERE id = :id AND lease_owner = :owner AND lease_epoch = :epoch
       AND state = 'running'
    """
)
_DELETE = text("DELETE FROM work_items WHERE id = :id")
_REQUEUE = text(
    """
    UPDATE work_items
       SET state = 'queued', lease_owner = NULL, lease_until = NULL,
           run_at = :run_at, attempts = :attempts, lease_losses = :lease_losses,
           first_unavailable_at = :first_unavailable_at,
           error_class = COALESCE(:error_class, error_class),
           last_error = COALESCE(:last_error, last_error)
     WHERE id = :id
    """
)
# A dead item's run_at becomes the moment it died, which is what retention
# measures and what an operator listing dead work sorts by.
_MARK_DEAD = text(
    """
    UPDATE work_items
       SET state = 'dead', lease_owner = NULL, lease_until = NULL, run_at = now(),
           attempts = :attempts, lease_losses = :lease_losses,
           error_class = :reason, last_error = :last_error
     WHERE id = :id
    """
)
_DUE_NOW = text(
    "UPDATE work_items SET run_at = least(run_at, now()) "
    "WHERE id = :id AND state = 'queued'"
)
_EXTEND = text(
    """
    UPDATE work_items SET lease_until = now() + make_interval(secs => :seconds)
     WHERE id = :id AND lease_owner = :owner AND lease_epoch = :epoch
       AND state = 'running'
    RETURNING id
    """
)
_OWNED_BY = text(
    """
    SELECT id FROM work_items WHERE lease_owner = :owner AND state = 'running'
    """
)
# ``epoch`` NULL: the shutdown release of everything this process owns, after
# every job of it has stopped.
_RELEASE_ONE = text(
    """
    SELECT id, ns, class, kind, payload, dedup_key, attempts, lease_losses,
           first_unavailable_at, run_at, created_at, lease_epoch
      FROM work_items
     WHERE id = :id AND lease_owner = :owner AND state = 'running'
       AND (CAST(:epoch AS bigint) IS NULL OR lease_epoch = :epoch)
       FOR UPDATE
    """
)
_EXPIRED = text(
    """
    SELECT id FROM work_items
     WHERE state = 'running' AND lease_until < now() AND class = :cls
     ORDER BY lease_until
     LIMIT :limit
    """
)
_LOCK_EXPIRED = text(
    """
    SELECT id, ns, class, kind, payload, dedup_key, attempts, lease_losses,
           first_unavailable_at, run_at, created_at, lease_epoch
      FROM work_items
     WHERE id = :id AND state = 'running' AND lease_until < now()
       FOR UPDATE SKIP LOCKED
    """
)


async def lock_owned(
    session: AsyncSession,
    item: ClaimedItem,
    owner: str,
    *,
    statement_timeout_ms: int | None = None,
) -> AttemptState | None:
    """Lock the item if this claim still holds it; ``None`` means the lease is lost.

    "This claim": the owner *and* the claim's epoch. A process that claimed the
    item again after its own lease expired is the same owner, and only the
    epoch tells its new run from the one still running.

    The row lock is the fence, and it dies at commit, so the session is marked
    as holding one: a connection-release helper the handler reaches (an
    authorization check commits a read-only session to hand its connection
    back) must not commit the fenced transaction half way through.

    ``statement_timeout_ms`` bounds every later statement of the transaction,
    set in the same round trip; only when the lock is held.
    """
    params: dict[str, object] = {
        "id": item.id,
        "owner": owner,
        "epoch": item.lease_epoch,
    }
    if statement_timeout_ms is None:
        statement = _LOCK_OWNED
    else:
        statement = _LOCK_OWNED_WITH_TIMEOUT
        params["timeout_ms"] = str(statement_timeout_ms)
    row = (await session.execute(statement, params)).first()
    if row is None:
        return None
    mark_transaction_scoped_lock(session)
    return AttemptState(row.attempts, row.lease_losses, row.first_unavailable_at)


def still_owned(session: Session, item: ClaimedItem, owner: str) -> bool:
    """Whether this claim holds the item at this moment; reads, locks nothing.

    Synchronous, for a session event (``before_commit``): under the asyncio
    extension those run inside the greenlet, where a sync ``execute`` does I/O.
    """
    params = {"id": item.id, "owner": owner, "epoch": item.lease_epoch}
    return session.execute(_STILL_OWNED, params).first() is not None


async def _rearm_periodic(
    session: AsyncSession, item: ClaimedItem, registry: WorkRegistry
) -> None:
    """Queue a periodic's next run ``every`` from now; nothing for other items."""
    periodic = registry.periodic_for(item.dedup_key)
    if periodic is not None:
        await WorkWriter(session, registry=registry).add(
            periodic.kind,
            item.ns,
            periodic.payload,
            dedup_key=periodic.dedup_key,
            delay=periodic.every,
        )


async def finish_item(
    session: AsyncSession, item: ClaimedItem, registry: WorkRegistry
) -> None:
    """Delete the finished item, re-arming it first if it is a periodic."""
    await _rearm_periodic(session, item, registry)
    await session.execute(_DELETE, {"id": item.id})


async def requeue(session: AsyncSession, item: ClaimedItem, decision: Requeue) -> bool:
    """Put the item back to ``queued``; ``False`` if it was coalesced away instead.

    When a twin with the same dedup key was queued while this one ran, the twin
    already stands for the work, and two queued items with one key cannot
    exist. So this one is deleted rather than requeued.
    """
    params = {
        "id": item.id,
        "run_at": decision.run_at,
        "attempts": decision.attempts,
        "lease_losses": decision.lease_losses,
        "first_unavailable_at": decision.first_unavailable_at,
        "error_class": decision.error_class,
        "last_error": decision.last_error,
    }
    if item.dedup_key is None:
        await session.execute(_REQUEUE, params)
        return True
    try:
        async with session.begin_nested():
            await session.execute(_REQUEUE, params)
    except IntegrityError:
        await session.execute(_DELETE, {"id": item.id})
        logger.info(
            "work.item.coalesced",
            item_id=str(item.id),
            kind=item.kind,
            dedup_key=item.dedup_key,
        )
        return False
    return True


async def mark_dead(
    uow: SqlAlchemyUnitOfWork,
    item: ClaimedItem,
    decision: Dead,
    spec: HandlerSpec | None,
    registry: WorkRegistry,
) -> None:
    """Move to ``dead`` and let the kind write its terminal domain state, together.

    A periodic is re-armed in the same transaction: one bad tick is kept dead
    for inspection, and the chain goes on ``every`` later. The dedup index
    covers queued items only, so the dead one does not block its successor.
    """
    await uow.session.execute(
        _MARK_DEAD,
        {
            "id": item.id,
            "attempts": decision.attempts,
            "lease_losses": decision.lease_losses,
            "reason": decision.reason,
            "last_error": decision.last_error,
        },
    )
    if spec is not None and spec.on_dead is not None:
        await spec.on_dead(
            DeadContext(
                uow=uow,
                item=item,
                reason=decision.reason,
                last_error=decision.last_error,
                work=WorkWriter(uow.session, registry=registry),
            )
        )
    await _rearm_periodic(uow.session, item, registry)
    dead_counter.add(
        1,
        {
            "work_class": item.cls.value,
            "work_kind": item.kind,
            "error_class": decision.reason,
        },
    )
    logger.warning(
        "work.item.dead",
        item_id=str(item.id),
        kind=item.kind,
        ns=item.ns,
        error_class=decision.reason,
        attempts=decision.attempts,
        lease_losses=decision.lease_losses,
    )


async def apply_decision(
    uow: SqlAlchemyUnitOfWork,
    item: ClaimedItem,
    decision: Decision,
    spec: HandlerSpec | None,
    registry: WorkRegistry,
) -> None:
    if isinstance(decision, Dead):
        await mark_dead(uow, item, decision, spec, registry)
    else:
        await requeue(uow.session, item, decision)


async def extend_lease(
    session: AsyncSession, item: ClaimedItem, owner: str, lease: timedelta
) -> bool:
    row = await session.scalar(
        _EXTEND,
        {
            "id": item.id,
            "owner": owner,
            "epoch": item.lease_epoch,
            "seconds": lease.total_seconds(),
        },
    )
    return row is not None


async def release(
    session: AsyncSession,
    item_id: object,
    owner: str,
    *,
    now: datetime,
    epoch: int | None = None,
) -> bool:
    """Hand an owned item back with nothing charged; ``False`` if it was not ours.

    ``epoch`` names the claim being released; ``None`` releases whatever this
    owner holds, which only shutdown does, once its jobs have stopped.
    """
    row = (
        await session.execute(
            _RELEASE_ONE, {"id": item_id, "owner": owner, "epoch": epoch}
        )
    ).first()
    if row is None:
        return False
    item = item_from_row(row)
    await requeue(
        session,
        item,
        Requeue(
            run_at=now,
            attempts=item.attempts,
            lease_losses=item.lease_losses,
            first_unavailable_at=item.first_unavailable_at,
            error_class=None,
            last_error=None,
        ),
    )
    # Due by the database's clock, which is what a claim compares with: a
    # worker whose clock runs ahead would otherwise requeue it slightly in the
    # future, and the next process would find nothing due.
    await session.execute(_DUE_NOW, {"id": item.id})
    # Another process may be idle and able to take it straight away.
    await session.execute(
        text("SELECT pg_notify(:channel, '')"), {"channel": wake_channel(item.cls)}
    )
    return True


async def owned_ids(session: AsyncSession, owner: str) -> list[object]:
    return [row.id for row in await session.execute(_OWNED_BY, {"owner": owner})]


async def expired_ids(session: AsyncSession, cls: str, limit: int) -> list[object]:
    rows = await session.execute(_EXPIRED, {"cls": cls, "limit": limit})
    return [row.id for row in rows]


async def lock_expired(session: AsyncSession, item_id: object) -> ClaimedItem | None:
    row = (await session.execute(_LOCK_EXPIRED, {"id": item_id})).first()
    return None if row is None else item_from_row(row)


def item_from_row(row: Row[tuple[object, ...]]) -> ClaimedItem:
    """Map a ``work_items`` row selected with the claimed-item columns."""
    mapping = row._mapping
    return ClaimedItem(
        id=mapping["id"],
        ns=mapping["ns"],
        cls=WorkClass(mapping["class"]),
        kind=mapping["kind"],
        payload=mapping["payload"],
        dedup_key=mapping["dedup_key"],
        attempts=mapping["attempts"],
        lease_losses=mapping["lease_losses"],
        first_unavailable_at=mapping["first_unavailable_at"],
        run_at=mapping["run_at"],
        created_at=mapping["created_at"],
        lease_epoch=mapping["lease_epoch"],
    )

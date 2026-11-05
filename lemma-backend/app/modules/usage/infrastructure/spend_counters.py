"""Each limit window's spend, kept as a running total the ledger agrees with.

Each limited window has a counter, and the counter's ``used_usd`` is what the
window has spent: settling a receipt adds its cost in the same transaction that
prices the ledger row, so the total moves exactly as the ledger does. Admitting
a request reads the counters and compares; it takes no lock and sums nothing.

That replaces recomputing every window from the ledger on every request, which
held each counter's row lock across a sum over the member's whole month -- six
of them per model request -- and so grew with the ledger and queued a member's
concurrent runs behind one another.

A running total is only as good as the writes that keep it, and two things
write past this one, which is why the old code recomputed every time:

* **Writers that do not keep counters.** The aggregate run recorder still
  writes ledger rows of its own. Inserting a priced SYSTEM row through the ORM
  marks every counter that row falls into for recounting, in the inserting
  transaction (``models._uncount_windows_of``), so no admission after its
  commit reads a total that misses it.
* **Policy that changes what a window counts.** A counter row is one
  organization, member, kind and start, but which rows it counts follows the
  limits: excluded organizations, excluded sources. The counter remembers the
  ``BudgetWindow.basis`` it was last counted under, and a window asking with a
  different one -- or with a different limit -- recounts it first.

A lifted limit is not a third: settlement charges every window a request
fell in that has a counter, limited or not, so a counter whose limit is lifted
keeps counting and is exact again the moment the same limit is restored.

Anything else -- a raw SQL edit of the ledger -- is caught by recounting any
counter not counted for ``usage_counter_refresh_seconds``. That bound, not
every request, is now how stale a total can be for a write nothing announced.

A recount never holds a settlement up for the length of its sum. It reads the
counter and the ledger in one snapshot and adds the difference,
``used += sum - used_then``, so a receipt settled meanwhile keeps its charge.
Recounters exclude one another with a transaction-scoped advisory lock; an
admission that finds somebody else already refreshing a current-but-old total
uses it rather than queueing. An ORM insert that marks a counter takes the
same lock, so the mark cannot land between a recount's sum and its write.
Settlement itself never recounts.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Row, Select, and_, case, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.log.log import get_logger
from app.modules.usage.config import usage_settings
from app.modules.usage.domain.accounting import BudgetWindow, money
from app.modules.usage.domain.events import UsageLimitWarningEvent
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import (
    COUNTER_LOCK_ORDER,
    COUNTER_RECOUNT_LOCK,
    UsageLimitCounter,
    UsageRecord,
    counter_recount_lock_key,
)

logger = get_logger(__name__)

type CounterKey = tuple[str, str, str, datetime]


@dataclass(frozen=True, slots=True)
class Counter:
    """One window's counter, as the statement that last touched it saw it."""

    id: UUID
    organization_id: UUID | None
    user_id: UUID | None
    window_kind: str
    window_start: datetime
    used: Decimal
    limit: Decimal | None
    warning_emitted: bool
    basis: str | None
    reconciled_at: datetime | None


_COUNTER_COLUMNS = (
    UsageLimitCounter.id,
    UsageLimitCounter.organization_id,
    UsageLimitCounter.user_id,
    UsageLimitCounter.window_kind,
    UsageLimitCounter.window_start,
    UsageLimitCounter.used_usd,
    UsageLimitCounter.limit_usd,
    UsageLimitCounter.warning_emitted,
    UsageLimitCounter.basis,
    UsageLimitCounter.reconciled_at,
)


#: One counter row as ``_COUNTER_COLUMNS`` selects or returns it.
type _CounterRow = Row[
    tuple[
        UUID,
        UUID | None,
        UUID | None,
        str,
        datetime,
        Decimal,
        Decimal | None,
        bool,
        str | None,
        datetime | None,
    ]
]


def _counter(row: _CounterRow) -> Counter:
    return Counter(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        window_kind=row.window_kind,
        window_start=row.window_start,
        used=row.used_usd,
        limit=row.limit_usd,
        warning_emitted=row.warning_emitted,
        basis=row.basis,
        reconciled_at=row.reconciled_at,
    )


def key(window: BudgetWindow) -> CounterKey:
    return window.kind, str(window.organization_id), str(window.user_id), window.start


def _counter_key(counter: Counter) -> CounterKey:
    return (
        counter.window_kind,
        str(counter.organization_id),
        str(counter.user_id),
        counter.window_start,
    )


type LockOrder = tuple[str, datetime, bool, int, bool, int]


def counter_lock_order(
    kind: str, start: datetime, organization_id: UUID | None, user_id: UUID | None
) -> LockOrder:
    """The order every writer takes counter rows in, so none waits on another
    in a cycle. ``models.COUNTER_LOCK_ORDER`` is the same order in SQL: kind,
    start, then organization and member with an absent one first."""
    return (
        kind,
        start,
        organization_id is not None,
        organization_id.int if organization_id is not None else 0,
        user_id is not None,
        user_id.int if user_id is not None else 0,
    )


def lock_order(window: BudgetWindow) -> LockOrder:
    return counter_lock_order(
        window.kind, window.start, window.organization_id, window.user_id
    )


def ordered(windows: list[BudgetWindow]) -> list[BudgetWindow]:
    """``windows`` without repeats, in lock order."""
    return sorted({key(w): w for w in windows}.values(), key=lock_order)


def _is(
    column: InstrumentedAttribute[UUID | None], value: UUID | None
) -> ColumnElement[bool]:
    return column.is_(None) if value is None else column == value


def _row_of(window: BudgetWindow) -> ColumnElement[bool]:
    return and_(
        UsageLimitCounter.window_kind == window.kind,
        UsageLimitCounter.window_start == window.start,
        _is(UsageLimitCounter.organization_id, window.organization_id),
        _is(UsageLimitCounter.user_id, window.user_id),
    )


def _window_cost(window: BudgetWindow) -> Select[tuple[Decimal]]:
    """What has been spent inside one window, recomputed from the ledger."""
    query = select(func.coalesce(func.sum(recorded_cost()), 0)).where(
        UsageRecord.profile_scope == "SYSTEM",
        UsageRecord.occurred_at >= window.start,
        UsageRecord.occurred_at < window.end,
    )
    if window.organization_id is not None:
        query = query.where(UsageRecord.organization_id == window.organization_id)
    if window.user_id is not None:
        query = query.where(UsageRecord.user_id == window.user_id)
    if window.source_types:
        query = query.where(UsageRecord.source_type.in_(window.source_types))
    if window.excluded_source_types:
        query = query.where(
            UsageRecord.source_type.notin_(window.excluded_source_types)
        )
    if window.excluded_organization_ids:
        query = query.where(
            or_(
                UsageRecord.organization_id.is_(None),
                UsageRecord.organization_id.notin_(window.excluded_organization_ids),
            )
        )
    return query


async def read(
    session: AsyncSession, windows: list[BudgetWindow]
) -> dict[CounterKey, Counter]:
    """The counters ``windows`` have, as they stand; a window without one is absent."""
    if not windows:
        return {}
    rows = await session.execute(
        select(*_COUNTER_COLUMNS).where(or_(*(_row_of(w) for w in windows)))
    )
    return {_counter_key(counter): counter for counter in map(_counter, rows)}


async def _existing(
    session: AsyncSession, windows: list[BudgetWindow]
) -> dict[CounterKey, Counter]:
    """Every window's counter, creating the ones that do not exist yet.

    A new counter has no basis, so it is counted from the ledger before
    anything trusts it: a window can start being limited long after it opened.
    """
    found = await read(session, windows)
    missing = [w for w in ordered(windows) if key(w) not in found]
    if missing:
        await session.execute(
            insert(UsageLimitCounter)
            .values(
                [
                    {
                        "organization_id": window.organization_id,
                        "user_id": window.user_id,
                        "window_kind": window.kind,
                        "window_start": window.start,
                        "window_end": window.end,
                        "used_usd": 0,
                        "reserved_usd": 0,
                        "limit_usd": window.limit,
                    }
                    for window in missing
                ]
            )
            .on_conflict_do_nothing()
        )
        found.update(await read(session, missing))
    return found


def _counts_for(counter: Counter, window: BudgetWindow) -> bool:
    """Whether the counter's total is this window's, as of its last write."""
    return counter.basis == window.basis and counter.limit == window.limit


def _due_for_refresh(counter: Counter) -> bool:
    refresh_after = timedelta(seconds=usage_settings.usage_counter_refresh_seconds)
    return (
        counter.reconciled_at is None
        or counter.reconciled_at <= datetime.now(timezone.utc) - refresh_after
    )


_TRY_RECOUNT_LOCK = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")


async def _recount(
    session: AsyncSession, counter: Counter, window: BudgetWindow
) -> tuple[Counter, Decimal]:
    """Set the counter to the ledger's total for the window, under its basis.

    The caller holds the counter's recount lock. The difference is applied
    rather than the sum stored, because a receipt settled between the read and
    the write is in the counter but was not in the snapshot the sum was taken
    from; storing the sum would drop its charge. Returns the difference too.
    """
    then, spent = (
        await session.execute(
            select(
                UsageLimitCounter.used_usd, _window_cost(window).scalar_subquery()
            ).where(UsageLimitCounter.id == counter.id)
        )
    ).one()
    drift = money(spent or 0) - then
    row = (
        await session.execute(
            update(UsageLimitCounter)
            .where(UsageLimitCounter.id == counter.id)
            .values(
                used_usd=UsageLimitCounter.used_usd + drift,
                basis=window.basis,
                reconciled_at=func.now(),
                limit_usd=window.limit,
                # Changing a limit resets its warning latch.
                warning_emitted=case(
                    (UsageLimitCounter.limit_usd.is_distinct_from(window.limit), False),
                    else_=UsageLimitCounter.warning_emitted,
                ),
            )
            .returning(*_COUNTER_COLUMNS)
        )
    ).one()
    return _counter(row), drift


async def _trusted(
    session: AsyncSession, counter: Counter, window: BudgetWindow
) -> Counter:
    """``counter``, recounted first if it cannot be trusted for ``window``."""
    if _counts_for(counter, window):
        if not _due_for_refresh(counter):
            return counter
        # Current but old. Somebody already refreshing it will finish without
        # this request waiting; the total in hand is good until then.
        mark_transaction_scoped_lock(session)
        if not await session.scalar(
            _TRY_RECOUNT_LOCK, counter_recount_lock_key(counter.id)
        ):
            return counter
    else:
        mark_transaction_scoped_lock(session)
        await session.execute(
            COUNTER_RECOUNT_LOCK, counter_recount_lock_key(counter.id)
        )
    current = (await read(session, [window]))[key(window)]
    trusted = _counts_for(current, window)
    if trusted and not _due_for_refresh(current):
        return current  # recounted by whoever held the lock first
    recounted, drift = await _recount(session, current, window)
    if trusted and drift:
        # Everything that writes the ledger keeps this total or marks it, so a
        # trusted total off by anything means a write that did neither.
        logger.warning(
            "usage.spend_counters.unannounced_ledger_write.degraded",
            counter_id=str(current.id),
            window_kind=current.window_kind,
            drift_usd=float(drift),
        )
    return recounted


async def counters(
    session: AsyncSession, windows: list[BudgetWindow]
) -> dict[CounterKey, Counter]:
    """Each window's counter, trusted to compare with its limit."""
    if not windows:
        return {}
    found = await _existing(session, windows)
    for window in ordered(windows):
        found[key(window)] = await _trusted(session, found[key(window)], window)
    return found


def exhausted(counters: dict[CounterKey, Counter], windows: list[BudgetWindow]) -> bool:
    """Whether a window has spent its limit. A window with no counter yet has
    spent nothing anybody has counted; the next admission counts it."""
    return any(
        w.limit is not None
        and (counter := counters.get(key(w))) is not None
        and counter.used >= w.limit
        for w in windows
    )


async def charge(
    session: AsyncSession, windows: list[BudgetWindow], cost: Decimal
) -> dict[CounterKey, Counter]:
    """Add one receipt's cost to the running total of each window it fell in.

    ``windows`` is every window the request fell in, limited or not. A lifted
    limit leaves its counter in place, and the same limit restored later
    trusts that counter again -- so it has to have kept counting meanwhile. A
    window that was never limited has no counter and costs nothing here.

    Settlement never recounts. The sum is still the window's total only if the
    counter was counted under the basis this receipt was admitted under; policy
    can move while a request is out, and a counter already recounted under the
    new rules, or marked for it, is left for the next admission to recount
    rather than trusted with a charge made under other ones.

    One statement for every window, its rows locked in lock order. A window
    with no counter is absent from the result: admission makes one for every
    window it admits under, so that is a window it will count, this row
    included, when next asked.
    """
    windows = ordered(windows)
    if not windows:
        return {}
    rows = await session.execute(
        update(UsageLimitCounter)
        .where(
            UsageLimitCounter.id.in_(
                select(UsageLimitCounter.id)
                .where(or_(*(_row_of(w) for w in windows)))
                .order_by(*COUNTER_LOCK_ORDER)
                .with_for_update()
            )
        )
        .values(
            used_usd=UsageLimitCounter.used_usd + cost,
            basis=case(
                *(
                    (
                        and_(_row_of(w), UsageLimitCounter.basis == w.basis),
                        UsageLimitCounter.basis,
                    )
                    for w in windows
                ),
                else_=None,
            ),
        )
        .returning(*_COUNTER_COLUMNS)
        .execution_options(synchronize_session=False)
    )
    return {_counter_key(counter): counter for counter in map(_counter, rows)}


async def warn_once(
    session: AsyncSession,
    counter: Counter,
    window: BudgetWindow,
    events: list[DomainEvent],
    fraction: Decimal,
) -> Counter:
    """Announce the first receipt that takes a window past ``fraction`` of its limit."""
    if (
        # A total waiting for a recount is not one to warn on; the first
        # receipt after the recount is.
        counter.basis != window.basis
        # Nor is a window whose limit was lifted, or changed since its counter
        # was last counted: the next admission takes up the new one.
        or window.limit is None
        or counter.limit != window.limit
        or counter.warning_emitted
        or counter.limit is None
        or counter.limit <= 0
        or counter.used < counter.limit * fraction
    ):
        return counter
    latched = await session.scalar(
        update(UsageLimitCounter)
        .where(
            UsageLimitCounter.id == counter.id,
            UsageLimitCounter.warning_emitted.is_(False),
        )
        .values(warning_emitted=True)
        .returning(UsageLimitCounter.id)
    )
    if latched is None:
        return counter
    events.append(
        UsageLimitWarningEvent(
            counter_id=counter.id,
            organization_id=counter.organization_id,
            user_id=counter.user_id,
            window_kind=counter.window_kind,
            used_usd=float(counter.used),
            limit_usd=float(counter.limit),
        )
    )
    return replace(counter, warning_emitted=True)

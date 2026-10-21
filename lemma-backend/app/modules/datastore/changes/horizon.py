"""The change log's gauges: the datastore's oldest open transaction, and how far
behind delivery runs.

The change log delivers a pod's changes only up to the oldest transaction still
open on the datastore database (its ``xmin`` horizon), so a transaction left
open stalls every trigger and live socket behind it. The work runtime's gauge
loop reads the main database's oldest transaction itself; this registers the
datastore database beside it, as ``lemma.work.oldest_transaction_age`` with
``database=datastore``. When the datastore shares the main database the two
series read the same value.

``lemma.changes.delivery_lag_seconds`` is what that stall costs a subscriber:
how long the oldest change a delivery found waiting for it has waited. Each
``changes.deliver`` notes, for its subscriber, ``now - at`` of the first change
it read, or of the first committed change it could not read yet (above the
horizon); ``0`` when it found nothing. The gauge is the largest of those this
process noted over the last one to two minutes, and ``0`` when it noted none.
Two approximations, both erring high or absent, never low for a delivery that
ran: ``at`` is the writing transaction's start, not its commit, so a long
writer reads as lag; and a subscriber no delivery ran for (no wake reached it)
is not measured until retention's backstop wakes it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

from app.core.infrastructure.work.gauges import (
    OLDEST_TRANSACTION_AGE,
    register_transaction_age_probe,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine

meter = metrics.get_meter(__name__)

#: One window of noted lags; the gauge reads this one and the one before, so a
#: reading never falls between windows into a false zero.
LAG_WINDOW_SECONDS = 60.0


async def datastore_oldest_transaction_age() -> float:
    async with get_datastore_engine().connect() as conn:
        return float(await conn.scalar(OLDEST_TRANSACTION_AGE) or 0.0)


register_transaction_age_probe("datastore", datastore_oldest_transaction_age)


@dataclass
class _LagWindows:
    started: float = 0.0
    current: float = 0.0
    previous: float = 0.0

    def roll(self, now: float) -> None:
        elapsed = now - self.started
        if elapsed < LAG_WINDOW_SECONDS:
            return
        # A whole window went by unnoted: what came before it is stale too.
        self.previous = self.current if elapsed < 2 * LAG_WINDOW_SECONDS else 0.0
        self.current = 0.0
        self.started = now

    def note(self, seconds: float, now: float) -> None:
        self.roll(now)
        self.current = max(self.current, seconds)

    def reading(self, now: float) -> float:
        self.roll(now)
        return max(self.current, self.previous)


_lag = _LagWindows()


def note_delivery_lag(seconds: float) -> None:
    """One delivery's lag, for ``lemma.changes.delivery_lag_seconds``."""
    _lag.note(max(0.0, seconds), time.monotonic())


def delivery_lag() -> float:
    """What ``lemma.changes.delivery_lag_seconds`` reads now."""
    return _lag.reading(time.monotonic())


def _observe_delivery_lag(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [Observation(delivery_lag())]


meter.create_observable_gauge(
    "lemma.changes.delivery_lag_seconds",
    callbacks=[_observe_delivery_lag],
    unit="s",
    description=(
        "Largest wait, over this process's change-log deliveries in the last "
        "one to two minutes, of the oldest change a delivery found unread or "
        "not yet final for its subscriber."
    ),
)

__all__ = [
    "LAG_WINDOW_SECONDS",
    "datastore_oldest_transaction_age",
    "delivery_lag",
    "note_delivery_lag",
]

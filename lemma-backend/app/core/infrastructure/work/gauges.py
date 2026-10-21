"""Work-table gauges: how much is waiting, and how long the oldest has waited.

A rate graph cannot tell a worker with nothing to do from one that has stopped
doing it. ``ready_oldest_age`` can, which is why it is the alerting signal per
class. Sampled by one query pair every interval rather than in an observable
callback, because the callbacks are synchronous and the reading is I/O. The
table holds only live work, so the scan is over what is actually pending.

Labels are the class and the state, never the namespace: a pod id as a metric
label is a series per tenant, which the export boundary strips anyway. Which
namespaces are busiest is logged instead (``work.namespaces.busiest``), at a
slower cadence and only while something is waiting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.work.db import AbstractSessionContext
from app.core.infrastructure.work.types import WorkClass
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
meter = metrics.get_meter(__name__)

_TOP_NAMESPACES = 20
#: Busiest-namespace log every this many samples (with the default interval,
#: every five minutes).
_BUSIEST_EVERY = 20
_STATES = ("queued", "running", "dead")

_BY_CLASS = text(
    """
    SELECT class,
           count(*) FILTER (WHERE state = 'queued') AS queued,
           count(*) FILTER (WHERE state = 'running') AS running,
           count(*) FILTER (WHERE state = 'dead') AS dead,
           count(DISTINCT ns) FILTER (WHERE state = 'queued') AS queued_ns,
           count(DISTINCT ns) FILTER (WHERE state = 'running') AS running_ns,
           EXTRACT(EPOCH FROM now() - min(run_at)
                   FILTER (WHERE state = 'queued' AND run_at <= now())) AS oldest
      FROM work_items
     GROUP BY class
    """
)
_TOP_NS = text(
    """
    SELECT class, ns, state, n FROM (
        SELECT class, ns, state, count(*) AS n,
               row_number() OVER (PARTITION BY class, state ORDER BY count(*) DESC) AS rank
          FROM work_items
         WHERE state IN ('queued', 'running')
         GROUP BY class, ns, state) ranked
     WHERE rank <= :limit
     ORDER BY class, state, n DESC
    """
)

#: How long the oldest open transaction on this database has been open. A
#: transaction holds the ``xmin`` horizon back, and the record change log
#: delivers nothing past it (design §15 row 4): this is what an alert on the
#: change-log horizon watches. The sampler's own session is excluded.
OLDEST_TRANSACTION_AGE = text(
    """
    SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(xact_start)), 0)
      FROM pg_stat_activity
     WHERE datname = current_database()
       AND backend_type = 'client backend'
       AND xact_start IS NOT NULL
       AND pid <> pg_backend_pid()
    """
)

type SessionOpener = Callable[[], AbstractSessionContext]
#: Reads one other database's oldest open transaction age, in seconds.
type TransactionAgeProbe = Callable[[], Awaitable[float]]

#: The main database is read on the gauges' own session; other databases (the
#: datastore's) register a probe here.
_transaction_age_probes: dict[
    str, TransactionAgeProbe
] = {}  # memory: bounded one per database (main, datastore)


def register_transaction_age_probe(database: str, probe: TransactionAgeProbe) -> None:
    """Sample ``database``'s oldest open transaction with the work gauges."""
    _transaction_age_probes[database] = probe


@dataclass
class _Snapshot:
    """Last successful reading. Empty means "not measured", never zero."""

    oldest: dict[str, float] = field(default_factory=dict)
    items: dict[tuple[str, str], int] = field(default_factory=dict)
    namespaces: dict[tuple[str, str], int] = field(default_factory=dict)
    transaction_age: dict[str, float] = field(default_factory=dict)
    samples: int = 0

    def forget(self) -> None:
        self.oldest, self.items, self.namespaces = {}, {}, {}
        self.transaction_age = {}


_snapshot = _Snapshot()
_incident = DependencyIncident("work.gauges", logger=logger)


def _observe_oldest(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(age, {"work_class": cls}) for cls, age in _snapshot.oldest.items()
    ]


def _observe(
    reading: Callable[[], dict[tuple[str, str], int]],
) -> Callable[[CallbackOptions], Iterable[Observation]]:
    def observe(options: CallbackOptions) -> Iterable[Observation]:
        del options
        return [
            Observation(n, {"work_class": cls, "state": state})
            for (cls, state), n in reading().items()
        ]

    return observe


meter.create_observable_gauge(
    "lemma.work.ready_oldest_age",
    callbacks=[_observe_oldest],
    unit="s",
    description="Age of the oldest due, unclaimed item per class.",
)
meter.create_observable_gauge(
    "lemma.work.items",
    callbacks=[_observe(lambda: _snapshot.items)],
    description="Work items per class and state (queued, running, dead).",
)


def _observe_transaction_age(options: CallbackOptions) -> Iterable[Observation]:
    del options
    return [
        Observation(age, {"database": database})
        for database, age in _snapshot.transaction_age.items()
    ]


meter.create_observable_gauge(
    "lemma.work.oldest_transaction_age",
    callbacks=[_observe_transaction_age],
    unit="s",
    description=(
        "Age of the oldest open transaction per database (main, datastore): "
        "the record change log's delivery horizon."
    ),
)
meter.create_observable_gauge(
    "lemma.work.namespaces",
    callbacks=[_observe(lambda: _snapshot.namespaces)],
    description="Namespaces (pods) with queued or running work, per class.",
)


async def sample(open_session: SessionOpener) -> None:
    busiest = _snapshot.samples % _BUSIEST_EVERY == 0
    async with open_session() as session:
        by_class = (await session.execute(_BY_CLASS)).all()
        top = (
            (await session.execute(_TOP_NS, {"limit": _TOP_NAMESPACES})).all()
            if busiest
            else []
        )
        main_age = float(await session.scalar(OLDEST_TRANSACTION_AGE) or 0.0)
    transaction_age = {"main": main_age}
    for database, probe in _transaction_age_probes.items():
        transaction_age[database] = float(await probe())
    _snapshot.transaction_age = transaction_age
    _snapshot.samples += 1
    # A class with nothing due has been measured: its oldest wait is zero. An
    # absent series would read as "no data" and silence the alert on it.
    _snapshot.oldest = {cls.value: 0.0 for cls in WorkClass} | {
        row._mapping["class"]: float(row.oldest)
        for row in by_class
        if row.oldest is not None
    }
    items: dict[tuple[str, str], int] = {}
    namespaces: dict[tuple[str, str], int] = {}
    for row in by_class:
        values = row._mapping
        for state in _STATES:
            items[(values["class"], state)] = int(values[state])
        namespaces[(values["class"], "queued")] = int(values["queued_ns"])
        namespaces[(values["class"], "running")] = int(values["running_ns"])
    _snapshot.items, _snapshot.namespaces = items, namespaces
    if top:
        _log_busiest(top)


def _log_busiest(rows: Iterable[object]) -> None:
    by_class: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        values = row._mapping  # type: ignore[attr-defined]
        states = by_class.setdefault(values["class"], {})
        states.setdefault(values["state"], {})[values["ns"]] = int(values["n"])
    for cls, states in by_class.items():
        # As "ns=count" text: a dict-valued field below error is dropped by
        # the log pipeline, which left this line carrying nothing.
        logger.info(
            "work.namespaces.busiest",
            work_class=cls,
            queued=_pairs(states.get("queued", {})),
            running=_pairs(states.get("running", {})),
        )


async def gauge_loop(open_session: SessionOpener, interval_seconds: float) -> None:
    while True:
        try:
            await sample(open_session)
            _incident.record_success()
        except (SQLAlchemyError, OSError) as exc:
            _snapshot.forget()
            _incident.record_failure(error_type=type(exc).__name__)
        await asyncio.sleep(interval_seconds)


def _pairs(counts: dict[str, int]) -> str:
    return ",".join(f"{ns}={count}" for ns, count in counts.items())

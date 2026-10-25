"""The vocabulary of the work runtime: classes, namespaces, claimed items, outcomes.

Everything here is plain data. Nothing imports SQLAlchemy, so the pure parts of
the runtime -- retry decisions, the claim walk, override parsing -- can be unit
tested without a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import JsonValue

#: A work item's payload: ids, not snapshots. Handlers read current state.
type JsonObject = dict[str, JsonValue]


class WorkClass(StrEnum):
    """A lane of work with its own slots, lease and per-namespace cap.

    A process runs any subset of classes (``WORK_CLASSES``), so the memory-heavy
    one can live in its own deployment and an OOM there cannot charge a lease
    loss to anything latency-sensitive.
    """

    EVENTS = "events"
    INTERACTIVE = "interactive"
    AUTOMATION = "automation"
    DELIVERY = "delivery"
    INGESTION = "ingestion"
    ADMIN = "admin"
    RECEIVERS = "receivers"


class WorkMode(StrEnum):
    """How a handler runs, which decides how its completion is fenced.

    ``FAST``: touches only the main database, and its effect and the item's
    deletion commit in one transaction under the item's row lock -- a crash
    rolls both back, so the effect is exactly-once. ``JOB``: external I/O under
    a heartbeated lease, finished by a fenced completion transaction; external
    effects are at-least-once and need an idempotency key.
    """

    FAST = "fast"
    JOB = "job"


SYSTEM_NAMESPACE = "system"
_NAMESPACE = re.compile(
    r"^(system(:[a-z][a-z_]*)?"
    r"|(pod|org):[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


def system_namespace(name: str) -> str:
    """A named system namespace: platform work that should take its own
    round-robin turn instead of queueing with the ``system`` periodics."""
    return validate_namespace(f"{SYSTEM_NAMESPACE}:{name}")


def pod_namespace(pod_id: UUID) -> str:
    return f"pod:{pod_id}"


def org_namespace(organization_id: UUID) -> str:
    return f"org:{organization_id}"


def validate_namespace(ns: str) -> str:
    """Refuse a namespace fairness and limits could not key on.

    A typo here would silently create a namespace of its own: its own cap, its
    own round-robin turn, invisible to a pause aimed at the real pod.
    """
    if not _NAMESPACE.fullmatch(ns):
        raise ValueError(
            "work namespace must be 'pod:<uuid>', 'org:<uuid>', 'system' or "
            f"'system:<name>', got {ns!r}"
        )
    return ns


@dataclass(frozen=True, slots=True)
class ClaimedItem:
    """A work item as its handler sees it, read at claim time."""

    id: UUID
    ns: str
    cls: WorkClass
    kind: str
    payload: JsonObject
    dedup_key: str | None
    attempts: int
    lease_losses: int
    first_unavailable_at: datetime | None
    run_at: datetime
    created_at: datetime
    #: Which claim this is. Every fenced write matches it, so a run whose
    #: lease was taken over -- by another process or by this one -- cannot.
    lease_epoch: int
    #: The enqueuer's W3C ``traceparent``, and a periodic's link to the run
    #: that re-armed it (``trace_carry``). Never part of ``payload``.
    traceparent: str | None = None
    trace_link: str | None = None


class UnavailableEvidence(StrEnum):
    """Why a dependency is believed down, as opposed to failing on this item.

    The distinction is the whole rule: an item that fails while the dependency
    serves others is the item's fault and is charged an attempt. Only evidence
    that the dependency is down for everyone earns a free retry.
    """

    CONNECTION_REFUSED = "connection_refused"
    HEALTH_CHECK_FAILING = "health_check_failing"
    CIRCUIT_OPEN = "circuit_open"
    RATE_LIMITED = "rate_limited"
    #: The schema is not the one this code expects, mid-rollout.
    SCHEMA_SKEW = "schema_skew"


class Unavailable(Exception):
    """A dependency is down; retry later without charging an attempt.

    Bounded by the kind's ``unavailable_budget``: a dependency that stays down
    longer than that sends the item to ``dead`` as ``unavailable_exhausted``.
    """

    def __init__(
        self,
        detail: str,
        *,
        evidence: UnavailableEvidence,
        retry_after: timedelta | None = None,
    ) -> None:
        super().__init__(detail)
        self.evidence = evidence
        self.retry_after = retry_after


#: How an ``Unavailable`` for Lemma's own database starts its detail, so a dead
#: hook can tell "the database was down" from a dependency of the kind's own.
DATABASE_UNAVAILABLE = "database unavailable"


def database_down(exc: BaseException) -> Unavailable:
    """The ``Unavailable`` a database connection failure is (see ``transient_errors``)."""
    down = Unavailable(
        f"{DATABASE_UNAVAILABLE}: {type(exc).__name__}",
        evidence=UnavailableEvidence.CONNECTION_REFUSED,
    )
    down.__cause__ = exc
    return down


def was_database_unavailable(last_error: str | None) -> bool:
    """Whether a dead item's last error was the database being unavailable."""
    return DATABASE_UNAVAILABLE in (last_error or "")


class ItemFailure(Exception):
    """This item failed; charge an attempt.

    Equivalent to raising anything else -- it exists so an adapter can say so
    explicitly when a dependency error is the item's fault (a 5xx on one
    document while the service's health check passes).
    """


class PermanentFailure(Exception):
    """Retrying cannot help (invalid input, a row that no longer exists); go dead now."""


class Defer(Exception):
    """Not now, and not a failure: requeue after ``delay`` without charging anything.

    For a domain gate a work-item concurrency key cannot express -- a per-pod
    cap held until a run is terminal.
    """

    def __init__(self, delay: timedelta) -> None:
        super().__init__(f"deferred for {delay}")
        self.delay = delay


class Released(Exception):
    """A job stopped at a safe point because the process is shutting down.

    The item goes back to ``queued`` with nothing charged, so a deploy never
    counts against it.
    """


class MaxRuntimeExceeded(Exception):
    """A job ran past its kind's ``max_runtime`` and was cancelled; it goes dead.

    Raised by the runtime, never by a handler. Dead rather than retried: a run
    that used its whole wall-clock allowance would most likely use it again.
    The kind's ``on_dead`` records the terminal state, with reason
    ``max_runtime``.
    """

    def __init__(self, limit: timedelta) -> None:
        super().__init__(f"ran past its {limit} time limit")
        self.limit = limit


class LeaseLost(Exception):
    """This process no longer owns the item; another worker has it or it is gone."""

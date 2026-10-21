"""The work runtime: durable work items in Postgres, written in the caller's transaction.

Public surface -- import from here, not from the submodules:

* ``work_handler`` / ``WorkMode`` / ``WorkClass`` / ``RetryPolicy`` -- register a kind.
* ``WorkWriter`` -- enqueue on an open unit of work.
* ``facts`` / ``Fact`` -- typed cross-module reactions, fanned out at write time.
* ``periodic`` -- recurring work, armed at worker boot; ``periodic_job`` -- a
  zero-argument maintenance coroutine as a periodic (what a cron became).
* ``Rate`` / ``RateUse`` -- named token buckets a kind's items draw from at claim.
* ``Unavailable`` / ``ItemFailure`` / ``PermanentFailure`` / ``Defer`` /
  ``Released`` / ``LeaseLost`` -- how a handler reports what happened.
* ``FastContext`` / ``JobContext`` / ``DeadContext`` -- what handlers receive.
"""

from app.core.infrastructure.work import maintenance as _maintenance  # noqa: F401  registers work.retention
from app.core.infrastructure.work.context import DeadContext, FastContext
from app.core.infrastructure.work.facts import Fact, FactBus, facts
from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.limits import (
    ConfiguredWorkLimitPort,
    NamespaceDirectory,
    WorkLimitPort,
    WorkLimits,
    install_work_limit_port,
    reset_work_limit_port,
)
from app.core.infrastructure.work.periodic_jobs import periodic_job
from app.core.infrastructure.work.rates import Rate, RateUse
from app.core.infrastructure.work.registry import (
    HandlerSpec,
    UnknownWorkKind,
    WorkRegistry,
    periodic,
    registry,
    work_handler,
)
from app.core.infrastructure.work.retry import DEFAULT_RETRY, Exponential, RetryPolicy
from app.core.infrastructure.work.types import (
    SYSTEM_NAMESPACE,
    system_namespace,
    ClaimedItem,
    Defer,
    ItemFailure,
    LeaseLost,
    MaxRuntimeExceeded,
    PermanentFailure,
    Released,
    Unavailable,
    UnavailableEvidence,
    WorkClass,
    WorkMode,
    database_down,
    org_namespace,
    pod_namespace,
    was_database_unavailable,
)
from app.core.infrastructure.work.writer import (
    CancelOutcome,
    RescheduleOutcome,
    WorkWriter,
)

__all__ = [
    "DEFAULT_RETRY",
    "SYSTEM_NAMESPACE",
    "system_namespace",
    "CancelOutcome",
    "ClaimedItem",
    "ConfiguredWorkLimitPort",
    "DeadContext",
    "Defer",
    "Exponential",
    "Fact",
    "FactBus",
    "FastContext",
    "HandlerSpec",
    "ItemFailure",
    "JobContext",
    "LeaseLost",
    "MaxRuntimeExceeded",
    "NamespaceDirectory",
    "PermanentFailure",
    "Rate",
    "RateUse",
    "Released",
    "RescheduleOutcome",
    "RetryPolicy",
    "Unavailable",
    "UnavailableEvidence",
    "database_down",
    "was_database_unavailable",
    "UnknownWorkKind",
    "WorkClass",
    "WorkLimitPort",
    "WorkLimits",
    "WorkMode",
    "WorkRegistry",
    "WorkWriter",
    "facts",
    "install_work_limit_port",
    "org_namespace",
    "periodic",
    "periodic_job",
    "pod_namespace",
    "registry",
    "reset_work_limit_port",
    "work_handler",
]

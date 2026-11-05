"""The :class:`LemmaModule` contract.

A ``LemmaModule`` is a *declarative* description of what a module contributes to
the running system. Every contribution is a **zero-arg thunk** (a callable that
returns the heavy objects) rather than the objects themselves, so importing a
module's ``module.py`` is cheap and free of import cycles: the controllers and
event handlers are imported only when the relevant entrypoint calls the thunk,
by which point that entrypoint is already the active importer.

Enable/disable is by *list membership*: a module absent from the assembled list
is simply not wired. A separate deployment (``lemma-cloud``) composes its own
list = open-source core modules + extra proprietary ones, and reuses the same
assembly functions (see ``app/core/registry/assembly.py``).

Cross-module needs are expressed as **ports & adapters**, not through this
contract: a consumer module declares a port (Protocol/ABC) for what it needs;
the open-source build wires a default adapter and ``lemma-cloud`` overrides it
via ordinary dependency injection. There is deliberately no admin field and no
provider registry here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # type-only — never imported at runtime, so no cycles / cost
    from fastapi import FastAPI

    from app.core.authorization.context import ResourceType
    from app.core.authorization.pod_liveness import PodLivenessReader
    from app.core.authorization.resource_names import ResourceNameTable
    from app.core.infrastructure.work.process import WorkerContext
    from app.core.infrastructure.work.limits import NamespaceDirectory
    from app.core.live.topics import LiveContribution
    from app.core.ports.plan_limits import PlanLimitsFactory


# --- thunk aliases -----------------------------------------------------------
# Each returns heavy objects lazily, inside the process/phase that needs them.
#
# PEP 695 `type` rather than a plain assignment: the right-hand side of a `type`
# statement is evaluated lazily, so a name that exists only under TYPE_CHECKING
# can be written as itself instead of quoted. The quoted form worked, but only a
# type checker ever read inside the string -- which is why three of these
# imports were reported as unused, and why a typo in one would have gone
# unnoticed at runtime.
type RouterProvider = Callable[[], Sequence[Any]]
"""Returns a sequence of ``fastapi.APIRouter`` to ``include_router`` (in order)."""

type ResourceNameProvider = Callable[
    [], Sequence[tuple[ResourceType, ResourceNameTable]]
]
"""Returns how this module's resource types map a human name to a row id.

A thunk, like `routers`: it is called at assembly, so declaring a table does not
drag a module's ORM models into every process that merely imports the registry.
"""

type PodLivenessProvider = Callable[[], PodLivenessReader]
"""Returns the reader core's deleted-pod guard asks whether a pod is live."""

type PlanLimitsProvider = Callable[[], PlanLimitsFactory]
"""Returns what builds the answerer for how many pods, members and how much
compute a plan allows. At most one module in a list may declare it."""

type NamespaceDirectoryProvider = Callable[[], NamespaceDirectory]
"""Returns what tells the work runtime which organization owns a namespace,
so per-org and per-slug limit overrides can apply to a pod's work."""

type LiveProvider = Callable[[], LiveContribution]
"""Returns the topics, calls and streams this module puts on the pod's live
socket. API process only; called once at assembly."""

type WorkRegistrar = Callable[[], None]
"""Imports the module's work kinds and fact subscriptions for side effects.

Called in every process, not only the worker: ``WorkWriter.add`` looks a kind up
to learn its class and payload, and ``facts.emit`` writes only the
subscriptions it can see, so an API process that never imported a subscriber
would commit a fact with nobody to react to it.
"""

type PeriodicRegistrar = Callable[[], None]
"""Imports the module's periodic work (``periodic_job``) for side effects.

Worker only: periodics are armed at worker boot and nothing else enqueues them,
so the API process need not import the sweeps (or pay for their imports).
"""

type AnalyticsRegistrar = Callable[[], None]
"""Imports the module's after-commit projections for side effects.

They register on the domain-event sink (``app.core.analytics.domain_events``)
by event type: analytics, and best-effort side effects such as a cache delete.
Called in every process, because whichever process commits a domain event is
the one that projects it.
"""

# Lifespan hooks are ``@asynccontextmanager`` functions entered inside an
# ``AsyncExitStack``; teardown happens automatically (LIFO) on stack unwind.
type ApiLifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]
type WorkerLifespan = Callable[[WorkerContext], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class LemmaModule:
    """Declarative description of one backend module's contributions."""

    name: str

    # --- API process ---
    routers: RouterProvider | None = None
    # What this module puts on the pod's live socket (`app/core/live`).
    live: LiveProvider | None = None
    api_lifespans: Sequence[ApiLifespan] = ()
    # --- Worker process (and the embedded worker of the single-process app) ---
    worker_lifespans: Sequence[WorkerLifespan] = ()

    # --- Every process ---
    # Work kinds and fact subscriptions; see ``WorkRegistrar``.
    register_work: WorkRegistrar | None = None
    # Recurring maintenance as periodic work; see ``PeriodicRegistrar``.
    register_periodic_work: PeriodicRegistrar | None = None
    # Analytics projections of committed domain events; see
    # ``AnalyticsRegistrar``.
    register_analytics: AnalyticsRegistrar | None = None

    # Grant APIs speak resource names; grant storage keeps ids. This is how one
    # becomes the other for this module's types.
    resource_names: ResourceNameProvider | None = None

    # A deleted pod stops answering for its contents. Core owns that rule and
    # this module owns the row it turns on.
    pod_liveness: PodLivenessProvider | None = None

    # What a plan allows. Declared by a deployment that sells plans; the
    # open-source list declares none, and nothing is limited.
    plan_limits: PlanLimitsProvider | None = None

    # Which organization owns a work namespace. Core's work runtime keys
    # limit overrides by org and slug and cannot read either; `mod:pod` can.
    namespace_directory: NamespaceDirectoryProvider | None = None

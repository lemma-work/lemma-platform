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
    from faststream.redis import RedisRouter

    from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext


# --- thunk aliases -----------------------------------------------------------
# Each returns heavy objects lazily, inside the process/phase that needs them.
RouterProvider = Callable[[], "Sequence[Any]"]
"""Returns a sequence of ``fastapi.APIRouter`` to ``include_router`` (in order)."""

EventRouterProvider = Callable[[], "Sequence[RedisRouter]"]
"""Returns FastStream ``RedisRouter`` objects to ``broker.include_router``."""

StreaqRegistrar = Callable[[], None]
"""Imports the module's ``@streaq_task``/``@streaq_cron`` modules for side effects."""

# Lifespan hooks are ``@asynccontextmanager`` functions entered inside an
# ``AsyncExitStack``; teardown happens automatically (LIFO) on stack unwind.
ApiLifespan = Callable[["FastAPI"], AbstractAsyncContextManager[None]]
WorkerLifespan = Callable[["AppWorkerContext"], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class LemmaModule:
    """Declarative description of one backend module's contributions."""

    name: str

    # --- API process ---
    routers: RouterProvider | None = None
    api_lifespans: Sequence[ApiLifespan] = ()

    # --- Worker process ---
    event_routers: EventRouterProvider | None = None
    register_streaq: StreaqRegistrar | None = None
    worker_lifespans: Sequence[WorkerLifespan] = ()

    # Redis Streams owned by this module's durable consumers. Publishers use
    # this topology before XADD, even in processes that never import handlers.
    stream_groups: Sequence[tuple[str, str]] = ()

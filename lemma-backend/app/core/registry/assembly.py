"""Assembly helpers that stitch a *provided* list of :class:`LemmaModule`.

These are deliberately small, composable loops rather than one monolithic
factory: ``app/app.py``'s ``create_app`` calls them as it is incrementally
migrated off its hand-written central lists, and ``lemma-cloud`` calls the same
loops with its own (larger) module list. Because every contribution is a thunk,
the heavy imports happen here — inside the active entrypoint — not when the
module list is constructed.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Sequence

from app.core.log.log import get_logger
from app.core.registry.contract import LemmaModule

if TYPE_CHECKING:
    from fastapi import FastAPI
    from faststream.redis import RedisBroker

    from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext

logger = get_logger(__name__)


def configure_stream_topology(modules: Sequence[LemmaModule]) -> None:
    from app.core.infrastructure.events.stream_subscriber import (
        declare_stream_groups,
    )

    declare_stream_groups(group for module in modules for group in module.stream_groups)


def include_module_routers(app: "FastAPI", modules: Sequence[LemmaModule]) -> None:
    """Include every module's API routers, in module-list then thunk order."""
    configure_stream_topology(modules)
    for module in modules:
        if module.routers is None:
            continue
        for router in module.routers():
            app.include_router(router)


def register_streaq_tasks(modules: Sequence[LemmaModule]) -> None:
    """Import each module's ``@streaq_task``/``@streaq_cron`` modules.

    Pure import-for-side-effect: the decorators register tasks on the shared
    ``streaq_worker`` singleton. Must run at import scope of the worker
    entrypoint (``app/events.py``), before ``broker.start()``.
    """
    for module in modules:
        if module.register_streaq is not None:
            module.register_streaq()


def import_module_tasks(modules: Sequence[LemmaModule]) -> None:
    """Import every module's task definitions, without wiring a broker.

    ``register_streaq`` is not the only thunk that carries ``@streaq_task``:
    plenty of modules define their tasks alongside their FastStream handlers,
    so importing ``event_routers`` is what registers those. The worker gets
    both because ``wire_module_events`` calls both. A process that only
    *publishes* jobs needs the same imports to know which lane each task runs
    on, but has no broker to include the routers on — so it comes here instead.
    """
    for module in modules:
        if module.register_streaq is not None:
            module.register_streaq()
        if module.event_routers is not None:
            module.event_routers()


def wire_module_events(modules: Sequence[LemmaModule], broker: "RedisBroker") -> None:
    """Register streaq tasks then include every module's FastStream routers."""
    configure_stream_topology(modules)
    register_streaq_tasks(modules)
    for module in modules:
        if module.event_routers is None:
            continue
        for router in module.event_routers():
            broker.include_router(router)


async def enter_api_lifespans(
    stack: AsyncExitStack, modules: Sequence[LemmaModule], app: "FastAPI"
) -> None:
    """Enter every module's API lifespan hooks on the given exit stack.

    Teardown is automatic and LIFO as the stack unwinds, so register core
    closers *after* this call to ensure they tear down last.
    """
    for module in modules:
        for make_cm in module.api_lifespans:
            await stack.enter_async_context(make_cm(app))


async def enter_worker_lifespans(
    stack: AsyncExitStack, modules: Sequence[LemmaModule], context: "AppWorkerContext"
) -> None:
    """Enter every module's worker lifespan hooks on the given exit stack."""
    for module in modules:
        for make_cm in module.worker_lifespans:
            await stack.enter_async_context(make_cm(context))

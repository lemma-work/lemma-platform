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
    from app.core.infrastructure.work.process import WorkerContext

logger = get_logger(__name__)


def configure_resource_names(modules: Sequence[LemmaModule]) -> None:
    """Collect how each module maps a resource name to a row id.

    Called from both process entry points: a grant resolved in the worker has
    to mean what it means in the API.
    """
    from app.core.authorization.resource_names import declare_resource_names

    for module in modules:
        if module.resource_names is None:
            continue
        declare_resource_names(module.resource_names())


def configure_pod_liveness(modules: Sequence[LemmaModule]) -> None:
    """Register the module that can say whether a pod is deleted."""
    from app.core.authorization.pod_liveness import declare_pod_liveness_reader

    for module in modules:
        if module.pod_liveness is None:
            continue
        declare_pod_liveness_reader(module.pod_liveness())


def configure_plan_limits(modules: Sequence[LemmaModule]) -> None:
    """Register the one module that says what plans allow, if there is one.

    Two would be a composition mistake with no right answer -- which plan wins
    -- so it stops the process rather than letting list order decide.
    """
    from app.core.plan_limits import declare_plan_limits

    declared = [module for module in modules if module.plan_limits is not None]
    if len(declared) > 1:
        names = ", ".join(module.name for module in declared)
        raise RuntimeError(f"more than one module declares plan_limits: {names}")
    provider = declared[0].plan_limits if declared else None
    declare_plan_limits(provider() if provider is not None else None)


def configure_namespace_directory(modules: Sequence[LemmaModule]) -> None:
    """Register the one module that can say which organization owns a pod.

    Two would disagree about whose limits a pod's work runs under, so it stops
    the process rather than letting list order decide.
    """
    from app.core.infrastructure.work.limits import declare_namespace_directory

    declared = [m for m in modules if m.namespace_directory is not None]
    if len(declared) > 1:
        names = ", ".join(module.name for module in declared)
        raise RuntimeError(
            f"more than one module declares namespace_directory: {names}"
        )
    provider = declared[0].namespace_directory if declared else None
    declare_namespace_directory(provider() if provider is not None else None)


def register_module_work(modules: Sequence[LemmaModule]) -> None:
    """Import every module's work kinds, fact subscriptions and analytics projections.

    Both the API and the worker need them: the API enqueues kinds and emits
    facts, the worker claims them. Idempotent, because importing is.
    """
    for module in modules:
        if module.register_work is not None:
            module.register_work()
        # Same reach, same reason: the process that commits a domain event is
        # the one whose sink projects it.
        if module.register_analytics is not None:
            module.register_analytics()


def register_module_periodic_work(modules: Sequence[LemmaModule]) -> None:
    """Import every module's periodic work: the worker's, armed at its boot."""
    register_module_work(modules)
    for module in modules:
        if module.register_periodic_work is not None:
            module.register_periodic_work()


def configure_live(modules: Sequence[LemmaModule]) -> None:
    """Put core's and every module's topics, calls and streams on the pod's
    live socket, and serve it."""
    from app.core.live import core_contribution
    from app.core.live.topics import install_live

    install_live(core_contribution())
    for module in modules:
        if module.live is not None:
            install_live(module.live())


def include_module_routers(app: "FastAPI", modules: Sequence[LemmaModule]) -> None:
    """Include every module's API routers, in module-list then thunk order,
    and the live sockets the modules feed: the pod's and the organization's."""
    from app.core.live.api import router as live_router
    from app.core.live.org_api import router as org_live_router

    register_module_work(modules)
    configure_resource_names(modules)
    configure_pod_liveness(modules)
    configure_plan_limits(modules)
    configure_namespace_directory(modules)
    configure_live(modules)
    for module in modules:
        if module.routers is None:
            continue
        for router in module.routers():
            app.include_router(router)
    app.include_router(live_router)
    app.include_router(org_live_router)


async def enter_api_lifespans(
    stack: AsyncExitStack, modules: Sequence[LemmaModule], app: "FastAPI"
) -> None:
    """Enter every module's API lifespan hooks on the given exit stack.

    Teardown is automatic and LIFO as the stack unwinds, so register core
    closers *after* this call to ensure they tear down last.
    """
    from app.core.observability.startup_timing import startup_step

    for module in modules:
        for make_cm in module.api_lifespans:
            hook = getattr(make_cm, "__name__", type(make_cm).__name__)
            async with startup_step(f"{module.name}.{hook}", service="lemma-api"):
                await stack.enter_async_context(make_cm(app))


async def enter_worker_lifespans(
    stack: AsyncExitStack, modules: Sequence[LemmaModule], context: "WorkerContext"
) -> None:
    """Enter every module's worker lifespan hooks on the given exit stack."""
    for module in modules:
        for make_cm in module.worker_lifespans:
            await stack.enter_async_context(make_cm(context))

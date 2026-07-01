"""Agent surfaces module registration."""

from contextlib import asynccontextmanager

from app.core.log.log import get_logger
from app.core.registry import LemmaModule

logger = get_logger(__name__)


def _routers():
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        router as surface,
        setup_guide_router as surface_setup_guide,
    )
    from app.modules.agent_surfaces.api.controllers.webhook_controller import (
        router as surface_public,
    )

    return [surface, surface_setup_guide, surface_public]


def _event_routers():
    from app.modules.agent_surfaces.events.handlers import router

    return [router]


async def _close_dedup_store() -> None:
    from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
        close_surface_event_dedup_store,
    )

    await close_surface_event_dedup_store()


@asynccontextmanager
async def _dedup_store_lifespan(app):
    """API process: close the surface webhook dedupe store on shutdown."""
    try:
        yield
    finally:
        await _close_dedup_store()


@asynccontextmanager
async def _surface_event_receiver(context):
    """Worker process: run native surface event receivers (Telegram polling /
    Slack socket mode) and close the dedupe store on shutdown."""
    import asyncio

    from app.modules.agent_surfaces.services.event_receiver_service import (
        SurfaceEventReceiverService,
    )

    receiver = SurfaceEventReceiverService(uow_factory=context.uow_factory)
    task = asyncio.create_task(receiver.run()) if receiver.should_start() else None
    if task is not None:
        logger.info("Native surface event receivers enabled for worker startup")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _close_dedup_store()


module = LemmaModule(
    name="agent_surfaces",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_dedup_store_lifespan,),
    worker_lifespans=(_surface_event_receiver,),
)

"""Agent surfaces module registration."""

from contextlib import asynccontextmanager

from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.core.registry import LemmaModule

logger = get_logger(__name__)


def _routers():
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        available_surfaces_router as surface_catalog,
        router as surface,
        platform_router as surface_platform_setup,
        setup_guide_router as surface_setup_guide,
    )
    from app.modules.agent_surfaces.api.controllers.notification_controller import (
        router as notifications,
    )
    from app.modules.agent_surfaces.api.controllers.user_surfaces_controller import (
        router as user_surfaces,
    )
    from app.modules.agent_surfaces.api.controllers.telegram_manager_controller import (
        router as telegram_manager,
    )
    from app.modules.agent_surfaces.api.controllers.webhook_controller import (
        router as surface_public,
    )

    return [
        surface,
        surface_setup_guide,
        surface_platform_setup,
        surface_catalog,
        telegram_manager,
        user_surfaces,
        notifications,
        surface_public,
    ]


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
async def _telegram_manager_webhook_lifespan(app):
    import asyncio

    from app.modules.agent_surfaces.services.telegram_manager_receiver import (
        run_telegram_manager_webhook_registration,
    )

    task = create_background_task(
        run_telegram_manager_webhook_registration(),
        name="telegram-manager-webhook-registration",
    )
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def _surface_event_receiver(context):
    """Worker process: run native surface event receivers (Telegram polling /
    Slack socket mode) and close the dedupe store on shutdown."""
    import asyncio

    from app.modules.agent_surfaces.services.event_receiver_service import (
        SurfaceEventReceiverService,
    )
    from app.modules.agent_surfaces.services.telegram_manager_receiver import (
        TelegramManagerPollingReceiver,
    )

    receiver = SurfaceEventReceiverService(uow_factory=context.uow_factory)
    manager_receiver = TelegramManagerPollingReceiver(
        uow_factory=context.uow_factory
    )
    task = (
        create_background_task(receiver.run(), name="surface-event-receiver")
        if receiver.should_start()
        else None
    )
    manager_task = (
        create_background_task(
            manager_receiver.run(),
            name="telegram-manager-receiver",
        )
        if manager_receiver.should_start()
        else None
    )
    try:
        yield
    finally:
        tasks = [candidate for candidate in (task, manager_task) if candidate]
        for candidate in tasks:
            candidate.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await _close_dedup_store()


module = LemmaModule(
    name="agent_surfaces",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(
        _dedup_store_lifespan,
        _telegram_manager_webhook_lifespan,
    ),
    worker_lifespans=(_surface_event_receiver,),
    stream_groups=(
        ("surface_events", "surface-webhook-events"),
        ("schedule_events", "surface-schedule-events"),
        ("pod_events", "surface-pod-deletion-events"),
        ("identity_events", "surface-identity-events"),
    ),
)

"""Agent surfaces module registration."""

from contextlib import asynccontextmanager

from app.core.request_context import create_background_task
from app.core.registry import LemmaModule


def _routers():
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        router as surface,
    )
    from app.modules.agent_surfaces.api.controllers.surface_catalog_controller import (
        available_surfaces_router as surface_catalog,
        setup_guide_router as surface_setup_guide,
    )
    from app.modules.agent_surfaces.api.controllers.slack_setup_controller import (
        platform_router as surface_platform_setup,
    )
    from app.modules.agent_surfaces.api.controllers.space_group_controller import (
        router as space_groups,
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
    from app.modules.agent_surfaces.api.controllers.web_widget_controller import (
        router as web_widgets,
    )
    from app.modules.agent_surfaces.api.controllers.public_page_controller import (
        router as public_page,
    )
    from app.modules.agent_surfaces.api.controllers.public_web_controller import (
        router as public_web,
    )
    from app.modules.agent_surfaces.api.controllers.contact_follow_up_controller import (
        public_router as public_contacts,
        router as contact_follow_ups,
    )

    return [
        surface,
        space_groups,
        surface_setup_guide,
        surface_platform_setup,
        surface_catalog,
        telegram_manager,
        user_surfaces,
        notifications,
        surface_public,
        web_widgets,
        public_page,
        public_web,
        contact_follow_ups,
        public_contacts,
    ]


@asynccontextmanager
async def _webhook_security_lifespan(app):
    """API process: say whether inbound webhooks have their authenticity checked.

    The first thing the ingress side says about itself. This process is the
    one that receives them, so this is where the answer is worth stating. On
    shutdown it lets go of the stranger-reply window's Redis client.
    """
    from app.modules.agent_surfaces.config import log_surface_webhook_security
    from app.modules.agent_surfaces.infrastructure.adapters.stranger_reply_window import (
        close_stranger_reply_window,
    )

    log_surface_webhook_security()
    try:
        yield
    finally:
        await close_stranger_reply_window()


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


def _register_work() -> None:
    # Every process. The API writes ingest, platform-update and delivery items
    # (webhooks, notifications) and so must know their kinds; the worker runs
    # them. Facts subscribed here (pod lifecycle, a run finishing, a changed
    # mobile) are written by whichever process commits the fact.
    import app.modules.agent_surfaces.events.ingest_work  # noqa: F401
    import app.modules.agent_surfaces.events.notification_work  # noqa: F401
    import app.modules.agent_surfaces.events.periodic_work  # noqa: F401
    import app.modules.agent_surfaces.events.platform_update_work  # noqa: F401
    import app.modules.agent_surfaces.events.pod_lifecycle_work  # noqa: F401
    import app.modules.agent_surfaces.events.receiver_work  # noqa: F401
    import app.modules.agent_surfaces.events.reply_for_run_work  # noqa: F401
    import app.modules.agent_surfaces.events.reply_work  # noqa: F401


module = LemmaModule(
    name="agent_surfaces",
    routers=_routers,
    register_work=_register_work,
    api_lifespans=(
        _webhook_security_lifespan,
        _telegram_manager_webhook_lifespan,
    ),
)

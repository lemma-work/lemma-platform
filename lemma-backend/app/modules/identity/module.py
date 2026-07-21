"""Identity module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.identity.api.controllers.user_controller import router as user
    from app.modules.identity.api.controllers.organization_controller import (
        router as organization,
    )
    from app.modules.identity.api.controllers.auth_controller import router as auth
    from app.modules.identity.api.controllers.email_bounce_controller import (
        router as email_bounce,
    )

    return [user, organization, auth, email_bounce]


def _event_routers():
    from app.modules.identity.events.handlers import router

    return [router]


@asynccontextmanager
async def _close_user_cache(app):
    """API process: close identity module Redis clients on shutdown."""
    try:
        yield
    finally:
        from app.modules.identity.infrastructure.user_cache import close_user_cache
        from app.modules.identity.services.desktop_auth_handoff import (
            get_desktop_auth_handoff_store,
        )
        from app.modules.identity.services.auth_abuse import close_auth_abuse_store
        from app.modules.identity.services.telegram_oidc import (
            close_telegram_oidc_store,
        )

        await close_user_cache()
        await get_desktop_auth_handoff_store().close()
        await close_auth_abuse_store()
        await close_telegram_oidc_store()


module = LemmaModule(
    name="identity",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_close_user_cache,),
    stream_groups=(("identity_events", "identity-email-events"),),
)

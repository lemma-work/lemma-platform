"""Identity module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.identity.api.controllers.user_controller import router as user
    from app.modules.identity.api.controllers.organization_controller import (
        router as organization,
    )
    from app.modules.identity.api.controllers.auth_controller import router as auth

    return [user, organization, auth]


def _event_routers():
    from app.modules.identity.events.handlers import router

    return [router]


@asynccontextmanager
async def _close_user_cache(app):
    """API process: close the cached-identity user store on shutdown."""
    try:
        yield
    finally:
        from app.modules.identity.infrastructure.user_cache import close_user_cache

        await close_user_cache()


module = LemmaModule(
    name="identity",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_close_user_cache,),
    stream_groups=(("identity_events", "identity-email-events"),),
)

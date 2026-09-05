"""App module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.apps.api.controllers.app_controller import router as app_router
    from app.modules.apps.api.controllers.public_app_controller import (
        router as public_app,
    )
    from app.modules.apps.api.controllers.public_sdk_controller import (
        router as public_sdk,
    )

    return [app_router, public_app, public_sdk]


def _register_streaq() -> None:
    import app.modules.apps.events.tasks  # noqa: F401


module = LemmaModule(name="apps", routers=_routers, register_streaq=_register_streaq)

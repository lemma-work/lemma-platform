"""Function module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.function.api.controllers.function_controller import (
        router as function,
    )

    from app.modules.function.api.controllers.function_runtime_controller import (
        router as function_runtime,
    )

    return [function, function_runtime]


def _event_routers():
    # Importing this module registers the function Streaq task and reconciler.
    from app.modules.function.events.handlers import router

    return [router]


module = LemmaModule(
    name="function",
    routers=_routers,
    event_routers=_event_routers,
)

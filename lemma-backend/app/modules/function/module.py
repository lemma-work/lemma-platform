"""Function module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.function.api.controllers.function_controller import (
        router as function,
    )

    return [function]


def _event_routers():
    from app.modules.function.events.handlers import router

    return [router]


module = LemmaModule(
    name="function",
    routers=_routers,
    event_routers=_event_routers,
    stream_groups=(("function_run_events", "function-run-events"),),
)

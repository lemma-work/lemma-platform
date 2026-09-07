"""Usage module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.usage.api.controllers import router as usage

    from app.modules.usage.api.self_controllers import router as self_usage

    return [usage, self_usage]


module = LemmaModule(
    name="usage",
    routers=_routers,
)

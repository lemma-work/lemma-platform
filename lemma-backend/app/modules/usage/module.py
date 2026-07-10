"""Usage module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.usage.api.controllers import router as usage

    return [usage]


module = LemmaModule(
    name="usage",
    routers=_routers,
)

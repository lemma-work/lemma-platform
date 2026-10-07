"""Decisions module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.decisions.api.controllers.decision_controller import (
        router as decisions,
    )

    return [decisions]


module = LemmaModule(name="decisions", routers=_routers)

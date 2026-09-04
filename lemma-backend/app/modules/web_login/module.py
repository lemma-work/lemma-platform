"""Web login module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.web_login.api.controllers.web_login_controller import (
        router as web_logins,
    )

    return [web_logins]


module = LemmaModule(name="web_login", routers=_routers)

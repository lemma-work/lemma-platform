"""Web login module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.web_login.api.controllers.sign_in_controller import (
        router as sign_in_requests,
    )
    from app.modules.web_login.api.controllers.web_login_controller import (
        router as web_logins,
    )

    # The sign-in routes first: both are under `/web-logins`, and the more
    # specific prefix has to match before the collection routes do.
    return [sign_in_requests, web_logins]


module = LemmaModule(name="web_login", routers=_routers)

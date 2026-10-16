"""MCP access module registration.

The pod MCP endpoint itself is mounted by `app/app.py` beside the agent host's,
because both are the same ASGI app. What this module contributes is everything
around it: discovery, the OAuth endpoints, consent, and connected clients.
"""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.mcp_access.config import mcp_access_settings

    if not mcp_access_settings.mcp_access_enabled:
        return []

    from app.modules.mcp_access.api.consent_controller import router as consent
    from app.modules.mcp_access.api.oauth_routes import oauth_router

    # Consent first: both sit under `/oauth`, and its routes are the ones that
    # need a session, so an overlap must never resolve to a public route.
    return [consent, oauth_router()]


def _register_work() -> None:
    # Every process: a pod deletion (API) writes this through `PodDeleted`.
    import app.modules.mcp_access.events.pod_lifecycle  # noqa: F401

    # Every process: subscribing (API) writes `changes.sync_subscribers`, and
    # the worker's change-log delivery finds this module's subscriber by prefix.
    import app.modules.mcp_access.events.event_deliveries  # noqa: F401


def _register_streaq() -> None:
    import app.modules.mcp_access.events.tasks  # noqa: F401


module = LemmaModule(
    name="mcp_access",
    routers=_routers,
    register_streaq=_register_streaq,
    register_work=_register_work,
)

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


def _event_routers():
    from app.modules.mcp_access.events import pod_lifecycle

    return [pod_lifecycle.router]


def _register_streaq() -> None:
    import app.modules.mcp_access.events.tasks  # noqa: F401


module = LemmaModule(
    name="mcp_access",
    routers=_routers,
    event_routers=_event_routers,
    register_streaq=_register_streaq,
    stream_groups=(("pod_events", "mcp-access-pod-events"),),
)

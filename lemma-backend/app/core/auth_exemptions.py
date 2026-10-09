"""Which requests the global authentication gate lets through, and why.

`verify_auth` is an app-level dependency, so every route needs a Lemma session
unless its path falls in one of the named groups below. There are three, and a
route belongs in exactly one:

- **Public**: everything under `PUBLIC_PREFIX`. Endpoints a browser or a pod
  app reaches with no Lemma session -- widget pages, short file links, SDK
  bundles, app assets, web chat, port previews. Each handler decides what its
  caller may do, usually from a capability in the URL. One prefix rather than a
  list, so a load balancer can apply its untrusted-traffic rules (rate limits,
  bot filtering) to `/public/*` without knowing the routes.
- **Self-authenticated**: endpoints that are not public but whose credential is
  one the session gate cannot read -- sign-in itself, OAuth, provider webhooks,
  and the machine endpoints that a signed-up person's credential stands behind
  (a paired computer, an MCP client). Each entry says what its credential is.
- **Legacy aliases**: old paths of routes that moved under `/public/`, kept for
  URLs already handed out, each with the date it is removed.

Health probes and API documentation are exempt too, and are none of these.

Entries match whole path segments: `/st` covers `/st` and `/st/...`, never
`/stats`. A `startswith` on the bare string made every exemption a claim on
every future path that happened to share its first letters.

`app/core/tests/unit/test_route_auth_classes.py` walks the built app and fails
when an exempt route is in no group, when an entry matches no route, and when
an alias is past its date.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

PUBLIC_PREFIX = "/public/"

HEALTH_AND_DOCS: tuple[str, ...] = (
    "/health",
    "/livez",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/scalar",
)

SELF_AUTHENTICATED: tuple[str, ...] = (
    "/st",  # SuperTokens sign-in, sign-up and session refresh (a mounted app)
    "/auth/cli/info",  # the CLI discovers the server before it has a session
    "/auth/cli/refresh",  # the CLI's refresh token is the credential
    # The provider's redirect back after OAuth; bound by the state parameter.
    "/connectors/connect-requests/oauth/callback",
    # Microsoft's redirect after a tenant admin consents; bound by the state.
    "/surfaces/teams/admin-consent/callback",
    # Platform webhooks; each handler verifies its platform's signature.
    "/surfaces/webhooks",
    # Schedule webhooks; the handler verifies the source's signature or secret.
    "/webhooks",
    # A paired computer has no user session and never will. Its link
    # WebSocket's first frame is the credential: a one-time pairing code, or
    # `hello` under the host secret. The user-facing host routes are under
    # `/me/runtime/...` and stay session-protected.
    "/agent-host",
    # The run-scoped MCP mount. It checks the run's own bearer token, and as a
    # mount it never reaches `verify_auth` at all.
    "/agent-runtime/pods",
    # Retired: the conversation MCP mount protocol-2 hosts called. It answers
    # only 410, and a 401 would read to the old bridge as retryable. Goes with
    # `agent_host_legacy_controller`.
    "/agent-runtime/conversations",
    # Outside MCP clients. The pod endpoint checks its own bearer token and
    # answers 401 with the challenge that starts a client's sign-in; the rest is
    # OAuth discovery and the endpoints a client calls before it has anything to
    # authenticate with. Consent and connected clients, also under `/oauth`,
    # need the person's session and are deliberately not listed.
    "/mcp",
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
    "/.well-known/oauth-protected-resource",
    "/oauth/authorize",
    "/oauth/token",
    "/oauth/register",
    "/oauth/revoke",
    # Payment-provider redirects (success/cancel pages) and its HMAC-signed
    # webhooks. Both are registered by lemma-cloud's billing module, so the open
    # app has no route here; see `ROUTED_OUTSIDE_THIS_APP`. The result pages are
    # browser-facing and belong under `/public/` -- a follow-up in lemma-cloud.
    "/billing/payment",
    "/billing/webhooks",
)

#: Entries whose routes another composition registers (`create_app(CLOUD_MODULES)`
#: in lemma-cloud). The gate builds the open app, so it cannot ask these to
#: match a route there.
ROUTED_OUTSIDE_THIS_APP = frozenset({"/billing/payment", "/billing/webhooks"})

#: Desktop sign-in: request creation and verifier exchange only. The
#: similarly-named `.../{request_id}/complete` must receive the browser's
#: session, which is why these are exact paths and not a prefix.
_DESKTOP_SIGN_IN = frozenset({"/auth/desktop/requests", "/auth/desktop/session"})

#: Sign-in steps that run before there is a session, exact and by method, plus
#: the email provider's bounce reports (signed with the provider's secret).
SIGN_IN_PATHS: dict[str, frozenset[str]] = {
    "GET": frozenset(
        {
            "/auth/altcha/config",
            "/auth/altcha/challenge",
            "/auth/telegram/config",
            "/auth/telegram/start",
            "/auth/telegram/callback",
        }
    ),
    "POST": _DESKTOP_SIGN_IN
    | frozenset(
        {
            "/auth/email/bounces",
            "/auth/email/bounces/resend",
            "/auth/email-code/browser",
            "/auth/email-code/continue",
            "/auth/email-code/start",
            "/auth/email-code/resend",
            "/auth/email-code/verify",
        }
    ),
}

#: WebSocket handshakes that authenticate themselves (cookie, bearer, or the
#: `access_token` query parameter a browser must use because it cannot set
#: headers on an upgrade). Exempt on the websocket scope only. The datastore
#: changes socket, `/pods/{pod_id}/datastore/changes`, is matched by
#: `_is_datastore_changes_ws_path` because its prefix is a pod id.
SELF_AUTHENTICATED_WEBSOCKETS: tuple[str, ...] = ("/workspace/browser/view",)

#: Old paths of routes that moved under `/public/`, and the day each goes. The
#: same handler serves both until then; the gate fails once a date passes, so
#: the alias cannot outlive the reason for it. Each date is the old URL's longest
#: life counted from the release that moved it, with room for that release to
#: wait in review.
LEGACY_ALIASES: dict[str, date] = {
    # Tool results saved the token-less widget URL into conversation history.
    "/widgets/serve": date(2026, 12, 15),
    # Short file links live at most seven days.
    "/s": date(2026, 11, 30),
    # Port grants are minted per preview and expire within the hour.
    "/workspace-ports": date(2026, 11, 30),
}


def under(path: str, prefix: str) -> bool:
    """Whether `path` is `prefix` or lies beneath it, segment by segment."""
    return path == prefix or path.startswith(prefix + "/")


def _under_any(path: str, prefixes: Iterable[str]) -> bool:
    return any(under(path, prefix) for prefix in prefixes)


def _is_surface_webhook_path(path: str) -> bool:
    """`/surfaces/{surface_id}/webhook`: a surface's own webhook, signed by its
    platform. Matched here because its prefix is an id."""
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "surfaces" or parts[2] != "webhook":
        return False
    try:
        UUID(parts[1])
    except ValueError:
        return False
    return True


def _is_datastore_changes_ws_path(path: str) -> bool:
    """Match ``/pods/{pod_id}/datastore/changes`` (the changes websocket).

    The handler authenticates the session itself (cookie or bearer), so the
    global HTTP auth dependency must let the handshake through.
    """
    parts = path.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "pods" or parts[2:4] != ["datastore", "changes"]:
        return False
    try:
        UUID(parts[1])
    except ValueError:
        return False
    return True


def _is_public_desktop_auth_path(path: str, method: str) -> bool:
    return method.upper() == "POST" and path in _DESKTOP_SIGN_IN


def _is_public_identity_auth_path(path: str, method: str) -> bool:
    return path in SIGN_IN_PATHS.get(method.upper(), frozenset())


def exemption_of(path: str, method: str) -> str | None:
    """The group that exempts this request from `verify_auth`, or None."""
    if path.startswith(PUBLIC_PREFIX):
        return "public"
    if _under_any(path, HEALTH_AND_DOCS):
        return "health and docs"
    if (
        _under_any(path, SELF_AUTHENTICATED)
        or _is_surface_webhook_path(path)
        or _is_public_identity_auth_path(path, method)
    ):
        return "self-authenticated"
    if _under_any(path, LEGACY_ALIASES):
        return "legacy alias"
    return None


def is_self_authenticated_websocket(path: str) -> bool:
    """Whether a websocket handshake on this path authenticates itself."""
    return _under_any(path, SELF_AUTHENTICATED_WEBSOCKETS) or (
        _is_datastore_changes_ws_path(path)
    )


__all__ = [
    "HEALTH_AND_DOCS",
    "LEGACY_ALIASES",
    "PUBLIC_PREFIX",
    "ROUTED_OUTSIDE_THIS_APP",
    "SELF_AUTHENTICATED",
    "SELF_AUTHENTICATED_WEBSOCKETS",
    "SIGN_IN_PATHS",
    "exemption_of",
    "is_self_authenticated_websocket",
    "under",
]

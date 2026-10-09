"""Every route the authentication gate lets through is in a named class.

`app.core.auth_exemptions` holds the classes: public (under `/public/`),
self-authenticated, legacy aliases, and health and docs. This walks the real
app, asks the real `verify_auth` about every route, and fails when

- a route gets through without being in any class -- an exemption somebody
  added ad hoc, or a mount or plain Starlette route, which never meets
  `verify_auth` at all;
- a class entry matches no route -- a dead exemption is a hole waiting for a
  route to fall into it;
- a legacy alias is past its removal date.

Run it with `uv run pytest app/core/tests/unit/test_route_auth_classes.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.routing import _iter_included_route_candidates as _effective_routes
from starlette.requests import HTTPConnection
from starlette.routing import BaseRoute, Mount, Route

from app.core import auth_exemptions as exemptions
from app.core.security import verify_auth

# Stands in for every path parameter: an id where the route wants one, and an
# ordinary segment where it wants a code, a token or a path.
_PLACEHOLDER = "019ba7e8-5115-7000-8000-000000000001"
_PARAMETER = re.compile(r"\{[^}]+\}")

_WHAT_TO_DO = (
    "Move a browser-facing endpoint under /public/ (keep the old path as a "
    "LEGACY_ALIASES entry with a removal date if URLs are already out). If it "
    "is sign-in, OAuth, a webhook or a machine credential, add it to "
    "SELF_AUTHENTICATED in app/core/auth_exemptions.py with a one-line reason."
)


@dataclass(frozen=True)
class _Endpoint:
    template: str
    path: str
    method: str
    kind: str  # "http", "websocket", or "unguarded" for mounts and plain routes

    def __str__(self) -> str:
        return f"{self.method} {self.template} ({self.kind})"


def _concrete(template: str) -> str:
    return _PARAMETER.sub(_PLACEHOLDER, template)


def _endpoints_of(route: BaseRoute) -> Iterator[_Endpoint]:
    template = getattr(route, "path", "")
    path = _concrete(template)
    if isinstance(route, APIWebSocketRoute):
        yield _Endpoint(template, path, "GET", "websocket")
    elif isinstance(route, APIRoute):
        for method in sorted(route.methods):
            yield _Endpoint(template, path, method, "http")
    elif isinstance(route, Mount | Route):
        # App-level dependencies reach only FastAPI's own route classes. A
        # mounted sub-app or a plain Starlette route never meets `verify_auth`,
        # so whatever it serves is exempt by construction.
        yield _Endpoint(template, path, "ANY", "unguarded")


@pytest.fixture(scope="module")
def endpoints() -> list[_Endpoint]:
    from app.modules.mcp_access.config import mcp_access_settings

    # Every optional surface on, so every entry has the chance to match.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_access_settings, "mcp_access_enabled", True)
        from app.app import create_app

        app = create_app()
    # FastAPI wraps each included router; this yields the effective routes,
    # with their full paths, exactly as the router matches them.
    return [
        endpoint
        for route in _effective_routes(app.routes)
        for endpoint in _endpoints_of(route)
    ]


async def _gate_lets_through(endpoint: _Endpoint) -> bool:
    if endpoint.kind == "unguarded":
        return True
    scope = {
        "type": endpoint.kind,
        "method": endpoint.method,
        "path": endpoint.path,
        "raw_path": endpoint.path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "scheme": "http" if endpoint.kind == "http" else "ws",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1),
    }
    if endpoint.kind == "websocket":
        del scope["method"]
    try:
        await verify_auth(HTTPConnection(scope))
    except Exception:
        return False
    return True


def _classes_of(endpoint: _Endpoint) -> list[str]:
    """Read from the class lists themselves, not from `exemption_of`, so an
    exemption added anywhere else is caught rather than agreed with."""
    path, method = endpoint.path, endpoint.method
    found = []
    if path.startswith(exemptions.PUBLIC_PREFIX):
        found.append("public")
    found += [
        f"self-authenticated {entry}"
        for entry in exemptions.SELF_AUTHENTICATED
        if exemptions.under(path, entry)
    ]
    if path in exemptions.SIGN_IN_PATHS.get(method, frozenset()):
        found.append("self-authenticated sign-in")
    if exemptions._is_surface_webhook_path(path):
        found.append("self-authenticated surface webhook")
    if endpoint.kind == "websocket" and exemptions.is_self_authenticated_websocket(
        path
    ):
        found.append("self-authenticated websocket")
    found += [
        f"legacy alias {entry}"
        for entry in exemptions.LEGACY_ALIASES
        if exemptions.under(path, entry)
    ]
    found += [
        f"health and docs {entry}"
        for entry in exemptions.HEALTH_AND_DOCS
        if exemptions.under(path, entry)
    ]
    return found


def test_the_walk_sees_the_whole_app(endpoints: list[_Endpoint]) -> None:
    """A walk that saw nothing would pass everything below."""
    kinds = {endpoint.kind for endpoint in endpoints}
    assert kinds == {"http", "websocket", "unguarded"}
    assert sum(endpoint.kind == "http" for endpoint in endpoints) > 200
    templates = {endpoint.template for endpoint in endpoints}
    assert {"/livez", "/st", "/public/web/widget.js"} <= templates


async def test_every_route_the_gate_lets_through_is_in_a_class(
    endpoints: list[_Endpoint],
) -> None:
    unclassified = [
        str(endpoint)
        for endpoint in endpoints
        if await _gate_lets_through(endpoint) and not _classes_of(endpoint)
    ]
    assert not unclassified, (
        "These routes need no session, but are in no class:\n  "
        + "\n  ".join(unclassified)
        + "\n"
        + _WHAT_TO_DO
    )


async def test_the_gate_lets_every_public_route_through(
    endpoints: list[_Endpoint],
) -> None:
    """`/public/` is a promise to the load balancer and to the browser alike:
    nothing under it may quietly start demanding a session."""
    guarded = [
        str(endpoint)
        for endpoint in endpoints
        if endpoint.path.startswith(exemptions.PUBLIC_PREFIX)
        and not await _gate_lets_through(endpoint)
    ]
    assert not guarded, (
        "These routes are under /public/ but the gate demands a session:\n  "
        + "\n  ".join(guarded)
        + "\nMove an endpoint that needs a session out from under /public/."
    )


def test_every_class_entry_matches_a_route(endpoints: list[_Endpoint]) -> None:
    paths = {endpoint.path for endpoint in endpoints}
    websockets = {e.path for e in endpoints if e.kind == "websocket"}
    by_method = {(e.method, e.path) for e in endpoints}

    dead = [
        f"SELF_AUTHENTICATED {entry}"
        for entry in exemptions.SELF_AUTHENTICATED
        if entry not in exemptions.ROUTED_OUTSIDE_THIS_APP
        and not any(exemptions.under(path, entry) for path in paths)
    ]
    dead += [
        f"LEGACY_ALIASES {entry}"
        for entry in exemptions.LEGACY_ALIASES
        if not any(exemptions.under(path, entry) for path in paths)
    ]
    dead += [
        f"SELF_AUTHENTICATED_WEBSOCKETS {entry}"
        for entry in exemptions.SELF_AUTHENTICATED_WEBSOCKETS
        if not any(exemptions.under(path, entry) for path in websockets)
    ]
    dead += [
        f"SIGN_IN_PATHS {method} {path}"
        for method, sign_in in exemptions.SIGN_IN_PATHS.items()
        for path in sign_in
        if (method, path) not in by_method
    ]
    assert not dead, (
        "These exemptions match no route. Delete them: an exemption with no "
        "route behind it makes public whatever is added there next.\n  "
        + "\n  ".join(dead)
    )


def test_no_legacy_alias_outlives_its_date() -> None:
    today = date.today()
    expired = [
        f"{path} (due {removal.isoformat()})"
        for path, removal in exemptions.LEGACY_ALIASES.items()
        if removal < today
    ]
    assert not expired, (
        "These legacy aliases are past their removal date. Delete the old-path "
        "route registration and its LEGACY_ALIASES entry:\n  " + "\n  ".join(expired)
    )


def test_the_classes_do_not_overlap() -> None:
    """An entry under `/public/` is already public; listing it elsewhere would
    make the reason in the list a lie about which rule applies."""
    named = [
        *exemptions.SELF_AUTHENTICATED,
        *exemptions.LEGACY_ALIASES,
        *exemptions.SELF_AUTHENTICATED_WEBSOCKETS,
    ]
    assert not [entry for entry in named if entry.startswith(exemptions.PUBLIC_PREFIX)]
    assert exemptions.ROUTED_OUTSIDE_THIS_APP <= set(exemptions.SELF_AUTHENTICATED)


@pytest.mark.parametrize(
    "path",
    ["/stats", "/status", "/webhooks-admin", "/sessions", "/scalars", "/publicity"],
)
def test_entries_cover_whole_segments_only(path: str) -> None:
    """`/st`, `/webhooks`, `/s` and `/scalar` once exempted anything that began
    with the same letters."""
    assert exemptions.exemption_of(path, "GET") is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/st", "self-authenticated"),
        ("/st/auth/signin", "self-authenticated"),
        ("/webhooks/github", "self-authenticated"),
        ("/s/abc", "legacy alias"),
        ("/s", "legacy alias"),
        ("/public/s/abc", "public"),
        ("/health/ready", "health and docs"),
    ],
)
def test_entries_cover_their_own_subtree(path: str, expected: str) -> None:
    assert exemptions.exemption_of(path, "GET") == expected

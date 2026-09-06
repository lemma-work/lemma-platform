"""API documentation is opt-in, everywhere.

`/openapi.json`, `/docs` and `/redoc` are unauthenticated, so serving them
publishes the shape of every endpoint to anyone who asks. Building the document
also costs 3.35s, measured in a production container — the largest item in a
cold start after the imports themselves — for something nothing in production
reads: both SDKs are generated at build time and the route inventory is a CI
gate.

**Not-serving is the only control these three have**, which is the reason this
is a flag rather than an auth dependency. FastAPI adds them itself as plain
Starlette `Route` objects, and app-level `dependencies=[Depends(verify_auth)]`
only reaches `APIRoute`s — verified in
`test_generated_doc_routes_cannot_carry_app_level_auth` below. `/scalar` is the
exception precisely because it is registered by hand and therefore *does* carry
the app-level dependency; that inconsistency is why the flag has to turn off all
four rather than leaning on auth for some of them.

Deliberately a flag rather than an inference from `environment`. "production" is
one value among four, and a deployment that forgets to set it, or sets something
unexpected, should fail closed rather than start publishing. The dev stack opts
in explicitly (`make init` writes `API_DOCS_ENABLED=true`).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def _all_paths(app) -> set[str]:
    """Every route path, including those inside an included router.

    FastAPI 0.141 wraps each `include_router` call in an `_IncludedRouter`, so a
    flat scan of `app.routes` sees only the handful registered directly on the
    app -- five of them here, against sixty-three routers. This walked flat
    until the health probes moved into a router of their own and `/livez`
    vanished from a test that had been reading the same blind spot all along.
    """
    found: set[str] = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        path = getattr(route, "path", None)
        if path:
            found.add(path)
        pending.extend(getattr(route, "routes", ()) or ())
        # FastAPI 0.141 keeps the included router on `original_router`, not on
        # `routes`; without this the walk stops at the wrapper.
        for attr in ("original_router", "router"):
            inner = getattr(route, attr, None)
            if inner is not None:
                pending.extend(getattr(inner, "routes", ()) or ())
    return found


def test_documentation_is_off_unless_asked_for() -> None:
    assert not Settings.model_construct(api_docs_enabled=False).api_docs_served()


def test_the_declared_default_is_off() -> None:
    """The value that ships. A deployment that sets nothing serves nothing."""
    assert Settings.model_fields["api_docs_enabled"].default is False


@pytest.mark.parametrize(
    "environment", ["local", "development", "testing", "production"]
)
def test_the_environment_does_not_decide(environment: str) -> None:
    """Same answer in every environment — only the flag decides."""
    off = Settings.model_construct(environment=environment, api_docs_enabled=False)
    on = Settings.model_construct(environment=environment, api_docs_enabled=True)

    assert not off.api_docs_served()
    assert on.api_docs_served()


def test_the_app_registers_no_documentation_route_when_off(monkeypatch) -> None:
    """Not just `/openapi.json` — `/docs`, `/redoc` and `/scalar` too.

    `/scalar` is the one worth pinning: it is registered by hand rather than by
    FastAPI, so turning the others off would have left a reference UI pointed at
    a document that no longer exists.
    """
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "api_docs_enabled", False)

    from app.app import create_app

    app = create_app()
    # Flat, deliberately: FastAPI registers `/docs`, `/redoc` and this repo's
    # `/scalar` directly on the app, and a deep walk would instead find a
    # module's own `/docs` route and fail for the wrong reason.
    directly_registered = {getattr(route, "path", None) for route in app.routes}

    assert app.openapi_url is None
    assert "/scalar" not in directly_registered
    assert "/docs" not in directly_registered
    assert "/redoc" not in directly_registered
    # The application's own routes are untouched. Deep, because the probes are
    # an included router.
    assert "/livez" in _all_paths(app)


def test_the_app_serves_them_when_asked(monkeypatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "api_docs_enabled", True)

    from app.app import create_app

    app = create_app()
    paths = _all_paths(app)

    assert app.openapi_url == "/openapi.json"
    assert "/scalar" in paths


def test_generated_doc_routes_cannot_carry_app_level_auth(monkeypatch) -> None:
    """The structural fact the flag exists for.

    An obvious review question is "why not just put auth on them instead of
    turning them off". This is why: FastAPI registers `/openapi.json`, `/docs`
    and `/redoc` itself, as plain Starlette `Route`s. App-level
    `dependencies=[...]` is applied by `APIRouter` when it builds an `APIRoute`,
    so those three never receive it — there is no `dependant` to put it on. Only
    `/scalar`, which this app registers by hand, carries it.

    Pinned rather than assumed because it is exactly the kind of thing a FastAPI
    upgrade could quietly change, in either direction, and either direction
    changes what "off" has to mean.
    """
    from fastapi.routing import APIRoute

    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "api_docs_enabled", True)

    from app.app import create_app

    app = create_app()
    assert len(app.router.dependencies) == 1, "app-level auth dependency is present"

    by_path = {getattr(route, "path", None): route for route in app.routes}
    for path in ("/openapi.json", "/docs", "/redoc"):
        route = by_path[path]
        assert not isinstance(route, APIRoute)
        assert getattr(route, "dependant", None) is None

    scalar = by_path["/scalar"]
    assert isinstance(scalar, APIRoute)
    assert len(scalar.dependant.dependencies) == 1


def test_the_dev_stack_opts_in() -> None:
    """`make dev` advertises /scalar; the generated .env has to deliver it.

    Checked against the Makefile because the failure is silent: the banner would
    still print the URL and the route would 404.
    """
    import pathlib

    makefile = pathlib.Path(__file__).resolve().parents[4].parent / "Makefile"
    text = makefile.read_text()

    assert "append API_DOCS_ENABLED true;" in text
    # Also in the required-key list, or an existing .env is judged complete and
    # never receives the new key.
    assert "for k in ENVIRONMENT DEBUG API_DOCS_ENABLED" in text

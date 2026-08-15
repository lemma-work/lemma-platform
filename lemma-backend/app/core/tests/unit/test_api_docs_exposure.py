"""API documentation is opt-in, everywhere.

`/openapi.json`, `/docs`, `/redoc` and `/scalar` are unauthenticated, so serving
them publishes the shape of every endpoint to anyone who asks. Building the
document also costs 3.35s, measured in a production container — the largest item
in a cold start after the imports themselves — for something nothing in
production reads: both SDKs are generated at build time and the route inventory
is a CI gate.

Deliberately a flag rather than an inference from `environment`. "production" is
one value among four, and a deployment that forgets to set it, or sets something
unexpected, should fail closed rather than start publishing. The dev stack opts
in explicitly (`make init` writes `API_DOCS_ENABLED=true`).
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_documentation_is_off_unless_asked_for() -> None:
    assert not Settings.model_construct(api_docs_enabled=False).api_docs_served()


def test_the_declared_default_is_off() -> None:
    """The value that ships. A deployment that sets nothing serves nothing."""
    assert Settings.model_fields["api_docs_enabled"].default is False


@pytest.mark.parametrize("environment", ["local", "development", "testing", "production"])
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
    paths = {getattr(route, "path", None) for route in app.routes}

    assert app.openapi_url is None
    assert "/scalar" not in paths
    assert "/docs" not in paths
    assert "/redoc" not in paths
    # The application's own routes are untouched.
    assert "/livez" in paths


def test_the_app_serves_them_when_asked(monkeypatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "api_docs_enabled", True)

    from app.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}

    assert app.openapi_url == "/openapi.json"
    assert "/scalar" in paths


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

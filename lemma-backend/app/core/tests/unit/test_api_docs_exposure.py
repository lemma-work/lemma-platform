"""Production serves no API documentation.

Two reasons, and the second is the one that matters. Building the OpenAPI
document costs ~3.35s measured in a production container — the second largest
item in a cold start after the imports — and nothing in production reads it:
both SDKs are generated at build time and the route inventory is a CI gate.
And `/openapi.json`, `/docs` and `/scalar` are unauthenticated, so serving them
publishes the shape of every endpoint to anyone who asks.

The override exists in both directions, because "we need the docs on staging"
and "we need them on this production deployment" are both real.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    return Settings.model_construct(**overrides)


@pytest.mark.parametrize("environment", ["local", "development", "testing"])
def test_docs_are_served_outside_production(environment: str) -> None:
    assert _settings(environment=environment, api_docs_enabled=None).api_docs_served()


def test_production_serves_no_docs_by_default() -> None:
    assert not _settings(
        environment="production", api_docs_enabled=None
    ).api_docs_served()


def test_production_can_opt_back_in() -> None:
    """A deployment that genuinely wants them should not have to patch code."""
    assert _settings(environment="production", api_docs_enabled=True).api_docs_served()


@pytest.mark.parametrize("environment", ["local", "development"])
def test_they_can_be_turned_off_anywhere(environment: str) -> None:
    assert not _settings(
        environment=environment, api_docs_enabled=False
    ).api_docs_served()


def test_the_app_hides_every_documentation_route_in_production(monkeypatch) -> None:
    """Not just `/openapi.json` — `/docs`, `/redoc` and `/scalar` too.

    `/scalar` is the one worth pinning: it is registered by hand rather than by
    FastAPI, so turning the others off would have left a reference UI pointed at
    a document that no longer exists.
    """
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "environment", "production")
    monkeypatch.setattr(config_module.settings, "api_docs_enabled", None)
    # Production refuses to start without a release identity; that guard is not
    # what this test is about. Set on the settings object, not the environment —
    # settings are already instantiated by the time a test runs.
    monkeypatch.setattr(config_module.settings, "release_sha", "a" * 40)

    from app.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}

    assert app.openapi_url is None
    assert "/scalar" not in paths
    assert "/docs" not in paths
    assert "/redoc" not in paths
    # The application's own routes are untouched.
    assert "/livez" in paths


def test_the_app_still_documents_itself_outside_production(monkeypatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "environment", "development")
    monkeypatch.setattr(config_module.settings, "api_docs_enabled", None)

    from app.app import create_app

    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}

    assert app.openapi_url == "/openapi.json"
    assert "/scalar" in paths

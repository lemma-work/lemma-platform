"""Settings guard for the app-serving base domain.

Apps are served by host at ``<public_slug>.<app_base_domain>``. There is no safe
default outside local/testing — the old ``apps.lemma.work`` default silently
mis-served every non-cloud install — so Settings fails loud when it is unset in
development/production. The local stack supplies ``APP_BASE_DOMAIN``.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_ambient_environment(monkeypatch):
    """Construct Settings from this file's arguments and nothing else.

    ``_env_file=None`` stops pydantic reading ``lemma-backend/.env``; it does
    not stop it reading the process environment, and a developer who has that
    file exported has ``DEBUG=true`` in theirs. Settings then fails on the debug
    validator rather than the one under test, so three of these assert the wrong
    error and the suite is red on a laptop and green in CI.

    Cleared wholesale rather than variable by variable: the next setting to
    acquire a validator would reintroduce this quietly, and Settings reads
    nothing else out of the environment that these cases want.
    """
    for name in list(os.environ):
        monkeypatch.delenv(name, raising=False)


def test_missing_app_base_domain_rejected_outside_local():
    for environment in ("development", "production"):
        with pytest.raises(ValueError, match="APP_BASE_DOMAIN"):
            Settings(environment=environment, app_base_domain="", _env_file=None)


def test_blank_app_base_domain_rejected_outside_local():
    # Whitespace-only is treated as unset.
    with pytest.raises(ValueError, match="APP_BASE_DOMAIN"):
        Settings(environment="production", app_base_domain="   ", _env_file=None)


def test_app_base_domain_allowed_when_set_outside_local():
    settings = Settings(
        environment="production",
        app_base_domain="apps.example.com",
        _env_file=None,
    )
    assert settings.app_base_domain == "apps.example.com"


def test_missing_app_base_domain_tolerated_in_local_and_testing():
    # Local installs get it from the stack; the test suite leaves it unset.
    for environment in ("local", "testing"):
        settings = Settings(environment=environment, app_base_domain="", _env_file=None)
        assert settings.app_base_domain == ""

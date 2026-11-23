"""Where private apps may open, and which cookie lets their viewers in.

Over HTTPS, at every app host, with the ``__Host-`` cookie. Over plain HTTP only
on a local stack that opted in with ``PRIVATE_APPS_OVER_HTTP``, and only at a
``.localhost`` app host, which never leaves the machine.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.apps.api.app_access import (
    ACCESS_COOKIE,
    LOCAL_ACCESS_COOKIE,
    private_app_host,
)
from app.modules.apps.config import AppsSettings, apps_settings

pytestmark = pytest.mark.unit


def _hosted(monkeypatch) -> None:
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")


def _make_dev(monkeypatch) -> None:
    """`make dev`'s addresses, before the stack opts in."""
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "api_url", "http://localhost:8710")
    monkeypatch.setattr(settings, "app_base_domain", "apps.lemma.localhost:8710")
    monkeypatch.setattr(apps_settings, "private_apps_over_http", False)


def _opt_in(monkeypatch) -> None:
    monkeypatch.setattr(apps_settings, "private_apps_over_http", True)


def test_https_hosts_keep_the_host_prefixed_secure_cookie(monkeypatch):
    _hosted(monkeypatch)
    for _ in ("as shipped", "even with the local setting somehow on"):
        host = private_app_host("desk.apps.example.test")

        assert host is not None
        assert host.origin == "https://desk.apps.example.test"
        assert host.secure is True
        assert host.cookie == ACCESS_COOKIE == "__Host-lemmaAppAccess"
        _opt_in(monkeypatch)


def test_local_http_does_not_open_private_apps_unless_asked(monkeypatch):
    _make_dev(monkeypatch)

    assert private_app_host("desk.apps.lemma.localhost:8710") is None


def test_local_http_opens_them_with_a_cookie_of_its_own(monkeypatch):
    _make_dev(monkeypatch)
    _opt_in(monkeypatch)
    # The default addresses, then one site (`make dev DEV_ONE_SITE=1`).
    for _ in ("localhost", "one site"):
        host = private_app_host("desk--r7.apps.lemma.localhost:8710")

        assert host is not None
        assert host.origin == "http://desk--r7.apps.lemma.localhost:8710"
        assert (host.slug, host.release_ref) == ("desk", "r7")
        assert host.secure is False
        assert host.cookie == LOCAL_ACCESS_COOKIE == "lemmaAppAccess"
        monkeypatch.setattr(settings, "api_url", "http://api.lemma.localhost:8710")


@pytest.mark.parametrize(
    "netloc",
    [
        "desk.apps.lemma.localhost:9999",  # another port
        "x.desk.apps.lemma.localhost:8710",  # a deeper subdomain
        "apps.lemma.localhost:8710",  # the bare base domain
        "localhost:8710",  # the API itself
    ],
)
def test_local_http_still_wants_the_apps_exact_host(monkeypatch, netloc):
    _make_dev(monkeypatch)
    _opt_in(monkeypatch)

    assert private_app_host(netloc) is None


def test_an_http_app_host_off_the_machine_is_never_opened(monkeypatch):
    """A cookie without Secure must not cross a network, so `.localhost` only."""
    _make_dev(monkeypatch)
    _opt_in(monkeypatch)
    monkeypatch.setattr(settings, "api_url", "http://api.dev.example.test")
    monkeypatch.setattr(settings, "app_base_domain", "apps.dev.example.test")

    assert private_app_host("desk.apps.dev.example.test") is None


def test_the_setting_is_ignored_outside_local_even_if_it_got_there(monkeypatch):
    _make_dev(monkeypatch)
    _opt_in(monkeypatch)
    monkeypatch.setattr(settings, "environment", "production")

    assert private_app_host("desk.apps.lemma.localhost:8710") is None


def test_the_setting_is_refused_at_startup_outside_local(monkeypatch):
    monkeypatch.delenv("PRIVATE_APPS_OVER_HTTP", raising=False)
    monkeypatch.setattr(settings, "environment", "production")

    assert AppsSettings(_env_file=None).private_apps_over_http is False
    with pytest.raises(ValueError, match="PRIVATE_APPS_OVER_HTTP"):
        AppsSettings(private_apps_over_http=True, _env_file=None)


def test_the_setting_is_off_unless_a_local_stack_asks(monkeypatch):
    monkeypatch.delenv("PRIVATE_APPS_OVER_HTTP", raising=False)
    monkeypatch.setattr(settings, "environment", "local")

    assert AppsSettings(_env_file=None).private_apps_over_http is False
    assert AppsSettings(
        private_apps_over_http=True, _env_file=None
    ).private_apps_over_http

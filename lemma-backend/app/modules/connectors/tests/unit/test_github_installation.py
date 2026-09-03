"""Resolving which installation a connected GitHub account speaks for.

The install redirect names it, and when it does that is the authority -- but it
only happens on a *first* install. This is what covers every other case, which
is most of them: reconnects, and anyone joining an organization where someone
else installed the App already.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.connectors.services.auth import github_installation
from app.modules.connectors.services.auth.github_installation import (
    bound_external_ref,
    install_url,
    resolve_installation,
)

pytestmark = pytest.mark.unit

CALLBACK = "https://api.example.com/oauth/callback?code=c&installation_id=158040062"


def _transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture
def github(monkeypatch):
    """Patch the client so a call is made but nothing leaves the process."""

    def install(*installations, status: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/user/installations"
            assert request.headers["Authorization"] == "Bearer gho_token"
            if status != 200:
                return httpx.Response(status)
            return httpx.Response(
                200, json={"installations": [{"id": i} for i in installations]}
            )

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = _transport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(github_installation.httpx, "AsyncClient", factory)

    return install


async def test_one_installation_is_the_answer(github):
    github(158040062)
    assert await resolve_installation("gho_token") == "158040062"


async def test_no_installation_binds_nothing(github):
    """Authorizing without installing gives a token that can see no repository.

    Better unbound and diagnosable than bound to a guess.
    """
    github()
    assert await resolve_installation("gho_token") is None


async def test_two_installations_are_left_unresolved(github):
    """Someone in two organizations that both installed the App.

    Picking whichever came back first would route the other organization's
    events at their pod -- the exact failure the per-account binding exists to
    prevent.
    """
    github(158040062, 200000001)
    assert await resolve_installation("gho_token") is None


async def test_a_failing_lookup_does_not_fail_the_connection(github):
    """The account is still worth having: everything that runs as the user works
    without an installation. Only triggers need one, and they say so."""
    github(status=503)
    assert await resolve_installation("gho_token") is None


class TestBinding:
    async def test_the_callback_still_wins_when_it_names_one(self, monkeypatch):
        async def _never(_token):
            raise AssertionError("GitHub was asked despite the callback naming it")

        monkeypatch.setattr(github_installation, "resolve_installation", _never)
        assert (
            await bound_external_ref("github", {"access_token": "gho_token"}, CALLBACK)
            == "158040062"
        )

    async def test_a_reconnect_falls_back_to_asking_github(self, github):
        github(158040062)
        # No `installation_id`: this is what an already-installed App's
        # authorization actually comes back with.
        assert (
            await bound_external_ref(
                "github",
                {"access_token": "gho_token"},
                "https://api.example.com/cb?code=c",
            )
            == "158040062"
        )

    async def test_other_connectors_never_reach_the_fallback(self, monkeypatch):
        async def _never(_token):
            raise AssertionError("a non-GitHub connector asked GitHub about itself")

        monkeypatch.setattr(github_installation, "resolve_installation", _never)
        assert await bound_external_ref("slack", {"access_token": "x"}, None) is None
        assert (
            await bound_external_ref("slack", {"raw_response": {"team_id": "T1"}}, None)
            == "T1"
        )

    async def test_a_credential_with_no_token_asks_nothing(self, monkeypatch):
        async def _never(_token):
            raise AssertionError("GitHub was asked with no token to ask with")

        monkeypatch.setattr(github_installation, "resolve_installation", _never)
        assert await bound_external_ref("github", {}, None) is None


def test_the_install_link_names_the_configured_app(monkeypatch):
    from app.modules.connectors.config import connector_settings

    monkeypatch.setattr(connector_settings, "connector_github_app_slug", "lemma-dev")
    assert install_url() == "https://github.com/apps/lemma-dev/installations/new"
    monkeypatch.setattr(connector_settings, "connector_github_app_slug", None)
    assert install_url() is None

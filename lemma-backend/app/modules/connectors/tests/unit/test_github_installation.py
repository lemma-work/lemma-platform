"""Resolving which installation a connected GitHub account speaks for.

The install redirect names it, and that is where it arrives first -- but it is
not taken on trust. GitHub warns the parameter is spoofable and `external_ref`
is the webhook routing key, so everything stated is proved against the token
that just came back. What is left over covers the cases the redirect never
reaches at all: reconnects, and anyone joining an organization where someone
else installed the App already.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.connectors.services.auth import github_installation
from app.modules.connectors.services.auth.github_installation import (
    InstallState,
    bound_external_ref,
    install_url,
    manage_installation_url,
    resolve_installation,
    resolve_outcome,
    verify_installation,
)

pytestmark = pytest.mark.unit

CALLBACK = "https://api.example.com/oauth/callback?code=c&installation_id=158040062"


@pytest.fixture
def github(monkeypatch):
    """Patch the client so a call is made but nothing leaves the process.

    Serves both endpoints the resolution touches: the list, and the per-install
    repositories call that proves a claim. `reachable` is which installation ids
    this token's owner can actually see, which is the whole question the
    verification asks.
    """

    def install(*installations, status: int = 200, reachable=(), verify_status=None):
        listed = [
            item if isinstance(item, dict) else {"id": item} for item in installations
        ]
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer gho_token"
            path = request.url.path
            seen.append(path)
            if path == "/user/installations":
                if status != 200:
                    return httpx.Response(status)
                return httpx.Response(200, json={"installations": listed})
            prefix, _, rest = path.removeprefix("/user/installations/").partition("/")
            assert rest == "repositories", path
            if verify_status is not None:
                return httpx.Response(verify_status)
            if prefix in {str(one) for one in reachable}:
                return httpx.Response(200, json={"repositories": []})
            return httpx.Response(404)

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(github_installation.httpx, "AsyncClient", factory)
        return seen

    return install


async def test_one_installation_is_the_answer(github):
    github(158040062)
    assert await resolve_installation("gho_token") == "158040062"


async def test_no_installation_binds_nothing(github):
    """Authorizing without installing gives a token that can see no repository.

    Better unbound and diagnosable than bound to a guess.
    """
    github()
    outcome = await resolve_outcome("gho_token")
    assert outcome.state is InstallState.INSTALL_REQUIRED
    assert outcome.installation_id is None


async def test_two_installations_ask_rather_than_guess(github):
    """Someone in two organizations that both installed the App.

    Picking whichever came back first would route the other organization's
    events at their pod -- the exact failure the per-account binding exists to
    prevent. Unlike before, the ambiguity is now answerable: the outcome carries
    both so the person can say which.
    """
    github(
        {"id": 158040062, "account": {"login": "octo", "type": "User"}},
        {
            "id": 200000001,
            "account": {"login": "acme", "type": "Organization"},
            "repository_selection": "selected",
        },
    )
    outcome = await resolve_outcome("gho_token")
    assert outcome.state is InstallState.CHOOSE_INSTALL
    assert outcome.installation_id is None
    assert [choice.account_login for choice in outcome.choices] == ["octo", "acme"]
    assert outcome.choices[1].is_organization
    assert not outcome.choices[1].covers_every_repository


async def test_a_failing_lookup_does_not_fail_the_connection(github):
    """The account is still worth having: everything that runs as the user works
    without an installation. Only triggers need one, and they say so."""
    github(status=503)
    assert await resolve_installation("gho_token") is None


async def test_a_request_awaiting_an_owner_is_not_an_install(github):
    """`setup_action=request` means an organization member asked and nothing was
    installed. There is no `installation_id` to look for, and telling them to
    connect again is advice that cannot succeed."""
    seen = github(158040062, reachable=(158040062,))
    outcome = await resolve_outcome("gho_token", setup_action="request")
    assert outcome.state is InstallState.PENDING_APPROVAL
    assert outcome.installation_id is None
    assert seen == [], "GitHub was asked about an installation that does not exist"


class TestVerification:
    async def test_a_claim_the_token_can_reach_is_believed(self, github):
        github(reachable=(158040062,))
        assert await verify_installation("gho_token", "158040062")

    async def test_a_claim_the_token_cannot_reach_is_refused(self, github):
        """The substitution GitHub warns about: anyone can hit the callback URL
        with an `installation_id` they do not own, and `external_ref` is what
        routes inbound deliveries."""
        github(reachable=())
        assert not await verify_installation("gho_token", "158040062")

    async def test_a_transport_failure_is_not_a_yes(self, github):
        github(verify_status=500)
        assert not await verify_installation("gho_token", "158040062")


class TestBinding:
    async def test_a_verified_callback_claim_is_taken_without_a_lookup(self, github):
        seen = github(reachable=(158040062,))
        assert (
            await bound_external_ref("github", {"access_token": "gho_token"}, CALLBACK)
            == "158040062"
        )
        assert "/user/installations" not in seen, "listed despite a proven claim"

    async def test_an_unproven_callback_claim_falls_back_to_the_truth(self, github):
        """A callback naming an installation this person cannot see is not a
        reason to bind them to it, and not a reason to give up either: what they
        *can* see is one call away."""
        github(200000001, reachable=(200000001,))
        assert (
            await bound_external_ref("github", {"access_token": "gho_token"}, CALLBACK)
            == "200000001"
        )

    async def test_a_reconnect_falls_back_to_asking_github(self, github):
        github(158040062, reachable=(158040062,))
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
        async def _never(*_args, **_kwargs):
            raise AssertionError("a non-GitHub connector asked GitHub about itself")

        monkeypatch.setattr(github_installation, "resolve_outcome", _never)
        assert await bound_external_ref("slack", {"access_token": "x"}, None) is None
        assert (
            await bound_external_ref("slack", {"raw_response": {"team_id": "T1"}}, None)
            == "T1"
        )

    async def test_a_credential_with_no_token_asks_nothing(self, monkeypatch):
        async def _never(*_args, **_kwargs):
            raise AssertionError("GitHub was asked with no token to ask with")

        monkeypatch.setattr(github_installation, "resolve_outcome", _never)
        assert await bound_external_ref("github", {}, None) is None


class TestLinks:
    def test_the_install_link_names_the_configured_app(self, monkeypatch):
        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(
            connector_settings, "connector_github_app_slug", "lemma-dev"
        )
        assert install_url() == "https://github.com/apps/lemma-dev/installations/new"
        monkeypatch.setattr(connector_settings, "connector_github_app_slug", None)
        assert install_url() is None

    def test_the_install_link_carries_the_state_that_completes_the_trip(
        self, monkeypatch
    ):
        """Without this the install redirect comes back with `installation_id`
        and no connect request to claim, and the callback rejects the one
        round trip that ever names the installation."""
        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(
            connector_settings, "connector_github_app_slug", "lemma-dev"
        )
        assert install_url("abc123").endswith("/installations/new?state=abc123")

    def test_an_organization_manages_its_installation_elsewhere(self):
        assert manage_installation_url(
            "42", account_login="acme", account_type="Organization"
        ) == ("https://github.com/organizations/acme/settings/installations/42")
        assert (
            manage_installation_url("42", account_login="octo", account_type="User")
            == "https://github.com/settings/installations/42"
        )


class TestTheSecondLegIsNotASecondConnect:
    """A chained install leg comes back through the same callback, and the
    connect saga would refuse it: the account it is completing was created by
    the first leg moments earlier and is healthy, so `_persist_account` reads
    the second arrival as somebody connecting GitHub twice and raises
    `ACCOUNT_ALREADY_CONNECTED`. The follow-up has its own shorter path.
    """

    def test_a_followup_request_is_recognisable_as_one(self) -> None:
        from app.modules.connectors.domain.connect_request import (
            ConnectRequestEntity,
            ConnectRequestStatus,
        )
        from app.modules.connectors.services.connect_request_lifecycle import (
            followup_account_id,
            followup_attributes,
        )
        from uuid import uuid4

        account_id = uuid4()
        request = ConnectRequestEntity(
            user_id=uuid4(),
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="github",
            status=ConnectRequestStatus.PENDING,
            attributes=followup_attributes(
                "state-1", account_id=account_id, provider_account_id="octocat"
            ),
        )
        assert followup_account_id(request) == str(account_id)

        ordinary = ConnectRequestEntity(
            user_id=uuid4(),
            organization_id=uuid4(),
            auth_config_id=uuid4(),
            connector_id="github",
            status=ConnectRequestStatus.PENDING,
            attributes={"state": "state-2"},
        )
        assert followup_account_id(ordinary) is None

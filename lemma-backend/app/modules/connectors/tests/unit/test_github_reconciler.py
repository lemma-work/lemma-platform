"""Resolving an installation without a redirect and without a webhook.

This is the load-bearing claim of the design: the connect redirect provably does
not always fire -- an organization owner approving a request hours later sends
nothing, and editing an installation's repository list sends nothing at all --
so the system has to be able to find out by asking.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.services.auth import github_reconciler
from app.modules.connectors.services.auth.github_installation import (
    InstallationChoice,
    InstallationOutcome,
    InstallState,
)
from app.modules.connectors.services.auth.github_reconciler import (
    GithubInstallationReconciler,
)

pytestmark = pytest.mark.unit


class _Cache:
    """A cache, so the test asserts on behaviour rather than on Redis."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_json(self, key: str):
        return self.values.get(key)

    async def set_json(self, key: str, value: object, **_kwargs) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _Repository:
    def __init__(self) -> None:
        self.updated: list[object] = []

    async def update(self, account):
        self.updated.append(account)
        return account


class _Service:
    def __init__(self) -> None:
        self.account_repository = _Repository()
        self.commits = 0
        self.uow = SimpleNamespace(commit=self._commit)

    async def _commit(self) -> None:
        self.commits += 1


def _account(**overrides):
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "connector_id": "github",
        "external_ref": None,
        "credentials": {"access_token": "gho_token"},
    }
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def answers(monkeypatch):
    """Whatever GitHub is pretending to say, and how often it was asked."""

    calls: list[str] = []

    def _set(outcome: InstallationOutcome):
        async def _resolve(token, **_kwargs):
            calls.append(token)
            return outcome

        monkeypatch.setattr(github_reconciler, "resolve_outcome", _resolve)

    async def _credentials(account, _user_id, **_kwargs):
        return account.credentials

    monkeypatch.setattr(github_reconciler, "fresh_credentials", _credentials)
    return _set, calls


async def test_an_installation_made_out_of_band_is_picked_up(answers):
    """Nobody came back through a redirect and no webhook was delivered. The
    account still ends up bound, which is the whole point."""
    setter, _ = answers
    setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
    service, account = _Service(), _account()

    outcome = await GithubInstallationReconciler(service, _Cache()).outcome(account)

    assert outcome.state is InstallState.READY
    assert account.external_ref == "158040062"
    assert service.account_repository.updated == [account]
    assert service.commits == 1


async def test_a_bound_account_costs_nothing(answers):
    """The common path. Asking would spend the person's own rate limit to be
    told what the column already says."""
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    account = _account(external_ref="158040062")

    outcome = await GithubInstallationReconciler(_Service(), _Cache()).outcome(account)

    assert outcome.installation_id == "158040062"
    assert calls == []


async def test_refresh_asks_again_even_when_bound(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.READY, installation_id="200000001"))
    account = _account(external_ref="158040062")
    service = _Service()

    await GithubInstallationReconciler(service, _Cache()).outcome(account, force=True)

    assert calls == ["gho_token"]
    assert account.external_ref == "200000001"


async def test_repeated_reads_of_an_unbound_account_ask_once(answers):
    """A page listing several accounts, or somebody clicking about, must not
    spend one GitHub call per render."""
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    reconciler = GithubInstallationReconciler(_Service(), _Cache())
    account = _account()

    first = await reconciler.outcome(account)
    second = await reconciler.outcome(account)

    assert first.state is second.state is InstallState.INSTALL_REQUIRED
    assert calls == ["gho_token"]


async def test_invalidating_makes_the_next_read_ask(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    cache = _Cache()
    reconciler = GithubInstallationReconciler(_Service(), cache)
    account = _account()

    await reconciler.outcome(account)
    await reconciler.invalidate(account.id)
    await reconciler.outcome(account)

    assert len(calls) == 2


async def test_several_installations_survive_the_cache(answers):
    """The picker needs the choices, so they have to round-trip."""
    setter, _ = answers
    setter(
        InstallationOutcome(
            InstallState.CHOOSE_INSTALL,
            choices=(
                InstallationChoice("1", "octo", "User", "all"),
                InstallationChoice("2", "acme", "Organization", "selected"),
            ),
        )
    )
    reconciler = GithubInstallationReconciler(_Service(), _Cache())
    account = _account()

    await reconciler.outcome(account)
    cached = await reconciler.outcome(account)

    assert cached.state is InstallState.CHOOSE_INSTALL
    assert [choice.account_login for choice in cached.choices] == ["octo", "acme"]
    assert cached.choices[1].manage_url.endswith(
        "/organizations/acme/settings/installations/2"
    )


async def test_an_ambiguous_answer_binds_nothing(answers):
    setter, _ = answers
    setter(InstallationOutcome(InstallState.CHOOSE_INSTALL, choices=()))
    service, account = _Service(), _account()

    await GithubInstallationReconciler(service, _Cache()).outcome(account)

    assert account.external_ref is None
    assert service.account_repository.updated == []


async def test_a_credential_with_no_token_is_not_a_missing_installation(answers):
    """Sending somebody to install an App they may already have is worse than
    saying nothing."""
    setter, calls = answers
    setter(InstallationOutcome(InstallState.READY, installation_id="1"))
    account = _account(credentials={})

    outcome = await GithubInstallationReconciler(_Service(), _Cache()).outcome(account)

    assert outcome.state is InstallState.INSTALL_REQUIRED
    assert calls == []


async def test_other_connectors_are_never_asked_about_installations(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    account = _account(connector_id="slack")

    outcome = await GithubInstallationReconciler(_Service(), _Cache()).outcome(account)

    assert outcome.state is InstallState.READY
    assert calls == []

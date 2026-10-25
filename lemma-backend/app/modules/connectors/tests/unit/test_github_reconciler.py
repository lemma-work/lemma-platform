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


class _Bindings:
    """The binding table, as the reconciler is handed it: nothing is bound to
    anybody yet unless a test says so. Its refusals are covered end to end
    against Postgres."""

    def __init__(self, owners: dict[str, object] | None = None) -> None:
        self.owners = dict(owners or {})
        self.claimed: list[tuple[str, bool]] = []

    async def owner(self, installation_id: str):
        return self.owners.get(installation_id)

    async def claimable_by(self, organization_id, installation_ids):
        return {
            one
            for one in installation_ids
            if self.owners.get(one) in (None, organization_id)
        }

    async def claim(self, account, installation_id: str, *, administered: bool):
        self.claimed.append((installation_id, administered))


async def _administered(_token: str, _installation_id: str) -> bool:
    return True


def _reconciler(service, cache, *, bindings=None, prove=_administered):
    return GithubInstallationReconciler(
        service, cache, bindings=bindings or _Bindings(), prove=prove
    )


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

    outcome = await _reconciler(service, _Cache()).outcome(account)

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

    outcome = await _reconciler(_Service(), _Cache()).outcome(account)

    assert outcome.installation_id == "158040062"
    assert calls == []


async def test_refresh_asks_again_even_when_bound(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.READY, installation_id="200000001"))
    account = _account(external_ref="158040062")
    service = _Service()

    await _reconciler(service, _Cache()).outcome(account, force=True)

    assert calls == ["gho_token"]
    assert account.external_ref == "200000001"


async def test_repeated_reads_of_an_unbound_account_ask_once(answers):
    """A page listing several accounts, or somebody clicking about, must not
    spend one GitHub call per render."""
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    reconciler = _reconciler(_Service(), _Cache())
    account = _account()

    first = await reconciler.outcome(account)
    second = await reconciler.outcome(account)

    assert first.state is second.state is InstallState.INSTALL_REQUIRED
    assert calls == ["gho_token"]


async def test_invalidating_makes_the_next_read_ask(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    cache = _Cache()
    reconciler = _reconciler(_Service(), cache)
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
    reconciler = _reconciler(_Service(), _Cache())
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

    await _reconciler(service, _Cache()).outcome(account)

    assert account.external_ref is None
    assert service.account_repository.updated == []


async def test_a_credential_with_no_token_is_not_a_missing_installation(answers):
    """Sending somebody to install an App they may already have is worse than
    saying nothing."""
    setter, calls = answers
    setter(InstallationOutcome(InstallState.READY, installation_id="1"))
    account = _account(credentials={})

    outcome = await _reconciler(_Service(), _Cache()).outcome(account)

    assert outcome.state is InstallState.INSTALL_REQUIRED
    assert calls == []


async def test_other_connectors_are_never_asked_about_installations(answers):
    setter, calls = answers
    setter(InstallationOutcome(InstallState.INSTALL_REQUIRED))
    account = _account(connector_id="slack")

    outcome = await _reconciler(_Service(), _Cache()).outcome(account)

    assert outcome.state is InstallState.READY
    assert calls == []


class TestTheCacheCannotBreakAnything:
    """One boundary now covers the cache read, write and invalidation.

    That consolidation is only safe if a failing cache stays survivable in all
    three directions, so each is pinned here: a read that fails is a miss and
    the answer is fetched, a write that fails is silent, and an invalidation
    that fails does not surface -- `bind_account_installation` commits before
    it invalidates, so an escaping error would report failure for work that
    already succeeded.
    """

    class _BrokenCache(_Cache):
        def __init__(self, *, failing: str) -> None:
            super().__init__()
            self.failing = failing

        async def get_json(self, key: str):
            if self.failing == "get":
                raise ConnectionError("redis is gone")
            return await super().get_json(key)

        async def set_json(self, key: str, value: object, **kwargs) -> None:
            if self.failing == "set":
                raise ConnectionError("redis is gone")
            await super().set_json(key, value, **kwargs)

        async def delete(self, key: str) -> None:
            if self.failing == "delete":
                raise ConnectionError("redis is gone")
            await super().delete(key)

    async def test_a_failing_read_is_a_miss_and_github_is_asked(self, answers, caplog):
        setter, calls = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="1"))
        reconciler = _reconciler(_Service(), self._BrokenCache(failing="get"))

        with caplog.at_level("WARNING"):
            outcome = await reconciler.outcome(_account())

        assert outcome.installation_id == "1"
        assert calls == ["gho_token"], "a broken cache stopped the lookup"
        assert "connectors.github_reconciler.cache_unavailable.degraded" in caplog.text

    async def test_a_failing_write_does_not_surface(self, answers, caplog):
        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="1"))
        service = _Service()
        reconciler = _reconciler(service, self._BrokenCache(failing="set"))
        account = _account()

        with caplog.at_level("WARNING"):
            outcome = await reconciler.outcome(account)

        # The answer is still recorded where it actually matters.
        assert outcome.installation_id == "1"
        assert account.external_ref == "1"
        assert "connectors.github_reconciler.cache_unavailable.degraded" in caplog.text

    async def test_a_failing_invalidation_does_not_surface(self, answers, caplog):
        """The one with a committed write behind it."""
        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="1"))
        reconciler = _reconciler(_Service(), self._BrokenCache(failing="delete"))

        with caplog.at_level("WARNING"):
            await reconciler.invalidate("some-account")

        assert "connectors.github_reconciler.cache_unavailable.degraded" in caplog.text


class TestNoClaimWithoutProof:
    """Audit H4: an installation nobody holds yet is bound only by its owner
    on GitHub's side. Seeing it is not enough."""

    async def test_an_unproven_claimant_binds_nothing_and_waits_on_an_owner(
        self, answers
    ):
        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
        service, account, bindings = _Service(), _account(), _Bindings()
        asked: list[tuple[str, str]] = []

        async def _not_theirs(token: str, installation_id: str) -> bool:
            asked.append((token, installation_id))
            return False

        outcome = await _reconciler(
            service, _Cache(), bindings=bindings, prove=_not_theirs
        ).outcome(account)

        assert asked == [("gho_token", "158040062")]
        assert outcome.state is InstallState.PENDING_APPROVAL
        assert account.external_ref is None
        assert bindings.claimed == []
        assert service.account_repository.updated == []

    async def test_one_the_organization_already_holds_needs_no_proof(self, answers):
        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
        account = _account()
        bindings = _Bindings({"158040062": account.organization_id})

        async def _never(_token: str, _installation_id: str) -> bool:
            raise AssertionError("asked GitHub for proof it did not need")

        outcome = await _reconciler(
            _Service(), _Cache(), bindings=bindings, prove=_never
        ).outcome(account)

        assert outcome.state is InstallState.READY
        assert account.external_ref == "158040062"


class TestTheStateIsWhatTheClaimMadeTrue:
    """Review of #1039: the outcome was cached READY before the claim ran, so
    a refusal left a READY with nothing bound and no token to mint."""

    class _Refusing(_Bindings):
        def __init__(self, refusal: Exception) -> None:
            super().__init__()
            self.refusal = refusal

        async def claim(self, account, installation_id: str, *, administered: bool):
            self.claimed.append((installation_id, administered))
            raise self.refusal

    async def test_a_github_admin_lemma_will_not_let_install_waits_on_one_who_may(
        self, answers
    ):
        from app.modules.connectors.domain.github_installation_errors import (
            InstallationClaimNotPermittedError,
        )

        setter, calls = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
        service, account, cache = _Service(), _account(), _Cache()
        bindings = self._Refusing(InstallationClaimNotPermittedError())
        reconciler = _reconciler(service, cache, bindings=bindings)

        first = await reconciler.outcome(account)
        cached = await reconciler.outcome(account)

        assert first.state is InstallState.PENDING_APPROVAL
        assert cached.state is InstallState.PENDING_APPROVAL
        assert calls == ["gho_token"], "the second read was not the cached one"
        assert account.external_ref is None
        assert service.account_repository.updated == []

    async def test_one_lost_to_another_org_is_not_ready_either(self, answers):
        from app.modules.connectors.domain.github_installation_errors import (
            GithubInstallationBoundElsewhereError,
        )

        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
        bindings = self._Refusing(GithubInstallationBoundElsewhereError())

        outcome = await _reconciler(_Service(), _Cache(), bindings=bindings).outcome(
            _account()
        )

        assert outcome.state is InstallState.INSTALL_REQUIRED

    async def test_the_claim_carries_whether_github_vouched(self, answers):
        """A binding this organization holds needs no proof, so none is asked
        for -- and the claim must not be told there was one, or it would
        stamp a binding migration 0063 derived as proven."""
        setter, _ = answers
        setter(InstallationOutcome(InstallState.READY, installation_id="158040062"))
        account = _account()
        held = _Bindings({"158040062": account.organization_id})
        await _reconciler(_Service(), _Cache(), bindings=held).outcome(account)
        unbound = _Bindings()
        await _reconciler(_Service(), _Cache(), bindings=unbound).outcome(_account())

        assert held.claimed == [("158040062", False)]
        assert unbound.claimed == [("158040062", True)]

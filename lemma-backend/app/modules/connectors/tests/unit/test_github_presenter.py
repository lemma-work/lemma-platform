"""Which GitHub identity runs an operation.

`github_token_kind` has been generated onto all 163 operations since the
catalog was written and read by nothing. These are the cases that make it mean
something -- and the fallbacks that let it be deployed before anyone has
reconnected.
"""

from __future__ import annotations

import pytest

from app.modules.connectors.domain.connector import ConnectorKind
from app.modules.connectors.domain.connector_operation import ResolvedOperation
from app.modules.connectors.domain.kinds import ExecutionRequest
from app.modules.connectors.services.auth.github_app import GitHubAppUnavailable
from app.modules.connectors.services.execution import github_presenter
from app.modules.connectors.services.execution.github_presenter import (
    GitHubCredentialPresenter,
)

pytestmark = pytest.mark.asyncio

USER_TOKEN = {"access_token": "gho_the_person", "token_type": "Bearer"}


def _request(
    token_kind: str | None,
    *,
    installation: str | None = "158040062",
    act_as: str = "app",
    name: str = "issues_create",
):
    return ExecutionRequest(
        connector_id="github",
        kind=ConnectorKind.HTTP,
        operation=ResolvedOperation(
            name=name,
            execution={"github_token_kind": token_kind} if token_kind else {},
        ),
        payload={},
        credentials=dict(USER_TOKEN),
        config={},
        deadline_seconds=45.0,
        # The account's binding, not the install's -- an App installed on two
        # organizations gives their accounts different installations.
        account_external_ref=installation,
        # Most of these ask to be the app, because that is the case
        # `github_token_kind` exists to answer. The default is "user"; see
        # `TestWhoTheCallerIs`.
        act_as=act_as,
    )


async def test_an_installation_operation_runs_as_the_app(monkeypatch):
    """The default for an agent's work: an identity that outlives whoever set
    the schedule up."""
    monkeypatch.setattr(github_presenter, "installation_token", _returns("ghs_the_app"))

    presented = await GitHubCredentialPresenter().present(_request("installation_ok"))

    assert presented["access_token"] == "ghs_the_app"
    assert presented["token_type"] == "Bearer"


async def test_a_user_only_operation_never_runs_as_the_app(monkeypatch):
    """These fourteen routes an installation token cannot reach at all --
    `/user/...` and gists. Two of them are load-bearing for pod publish."""
    monkeypatch.setattr(github_presenter, "installation_token", _explodes())

    presented = await GitHubCredentialPresenter().present(_request("user_only"))

    assert presented == USER_TOKEN


async def test_an_install_with_no_installation_yet_keeps_working(monkeypatch):
    """The half-migrated case. An install carrying only a user token still has
    a working token; going quiet here would break every operation on it for the
    sake of an identity it has never had."""
    monkeypatch.setattr(github_presenter, "installation_token", _explodes())

    presented = await GitHubCredentialPresenter().present(
        _request("installation_ok", installation=None)
    )

    assert presented == USER_TOKEN


async def test_an_unavailable_app_degrades_the_identity_not_the_operation(
    monkeypatch,
):
    """No private key configured, or the App was uninstalled since the id was
    stored. The user token is a real credential and a narrower one."""

    async def refuse(installation_id, **kwargs):
        raise GitHubAppUnavailable("no key")

    monkeypatch.setattr(github_presenter, "installation_token", refuse)

    presented = await GitHubCredentialPresenter().present(_request("installation_ok"))

    assert presented == USER_TOKEN


async def test_an_operation_declaring_no_kind_is_treated_as_installation_ok(
    monkeypatch,
):
    """A discovered operation, or one predating the classification. Defaulting
    to the App matches the 149-to-14 split and keeps the fallback above."""
    monkeypatch.setattr(github_presenter, "installation_token", _returns("ghs_x"))

    presented = await GitHubCredentialPresenter().present(_request(None))

    assert presented["access_token"] == "ghs_x"


def _returns(token: str):
    async def _token(installation_id, **kwargs):
        return token

    return _token


def _explodes():
    async def _token(installation_id, **kwargs):
        raise AssertionError("must not mint for this operation")

    return _token


async def test_the_dispatcher_presents_before_it_executes(monkeypatch):
    """The wiring, not just the decision.

    A presenter that is never called is the same as no presenter, and the unit
    tests above would pass either way. This asserts the executor receives the
    presented credential -- through the dispatcher, which is the one place every
    kind passes through.
    """
    from app.modules.connectors.domain.kinds import ExecutionRequest as Req
    from app.modules.connectors.infrastructure.kinds.registry import KindRegistry
    from app.modules.connectors.services.execution.credential_presenter import (
        PresenterRegistry,
    )
    from app.modules.connectors.services.execution.dispatcher import KindDispatcher

    seen: dict[str, object] = {}

    class _Executor:
        async def execute(self, request: Req):
            seen.update(request.credentials)
            return {"ok": True}

    class _Registry(KindRegistry):
        def __init__(self):  # noqa: D107 - stand-in, not the real registry
            pass

        def get(self, kind):
            from types import SimpleNamespace

            return SimpleNamespace(executor=_Executor(), discoverer=None)

    class _Swaps:
        async def present(self, request):
            return {"access_token": "ghs_presented", "token_type": "Bearer"}

    dispatcher = KindDispatcher(
        _Registry(), presenters=PresenterRegistry({"github": _Swaps()})
    )

    await dispatcher.execute(_request("installation_ok"))

    assert seen["access_token"] == "ghs_presented", (
        "the executor ran with the stored credential, so the presenter is not wired"
    )


async def test_the_installation_comes_from_the_account_not_the_install(monkeypatch):
    """One Lemma install of the App serves every organization that authorized
    it, and each has its own installation. Reading the id from the install
    config -- which they share -- would mint against one organization and hand
    the token to another's account.
    """
    minted_for: list[str] = []

    async def _token(installation_id, **kwargs):
        minted_for.append(installation_id)
        return f"ghs_for_{installation_id}"

    monkeypatch.setattr(github_presenter, "installation_token", _token)

    first = _request("installation_ok", installation="111")
    second = _request("installation_ok", installation="222")
    # Both accounts sit on the same install, which knows nothing about either.
    assert first.config == second.config == {}

    presenter = GitHubCredentialPresenter()
    assert (await presenter.present(first))["access_token"] == "ghs_for_111"
    assert (await presenter.present(second))["access_token"] == "ghs_for_222"
    assert minted_for == ["111", "222"]


class TestWhoTheCallerIs:
    """Two questions, not one -- and conflating them was a real bug.

    `github_token_kind` says what a GitHub App is *permitted* to do. It says
    nothing about whether a given caller *should be* the app. Pod publish writes
    through `git_create_blob`/`git_create_tree`/`git_create_commit`/
    `git_update_ref`, and every one of those is `installation_ok` -- so keying
    only on the operation silently republished everybody's pods as commits
    authored by a bot, and made import unable to read a repository the App was
    never installed on.
    """

    async def test_a_caller_that_says_nothing_stays_the_person(self, monkeypatch):
        monkeypatch.setattr(github_presenter, "installation_token", _explodes())

        presented = await GitHubCredentialPresenter().present(
            _request("installation_ok", act_as="user")
        )

        assert presented == USER_TOKEN

    async def test_the_default_is_the_person(self):
        """Not merely reachable -- the default a caller gets by omission."""
        request = _request("installation_ok")
        assert type(request).__dataclass_fields__["act_as"].default == "user"

    @pytest.mark.parametrize(
        "operation",
        ["git_create_blob", "git_create_tree", "git_create_commit", "git_update_ref"],
    )
    async def test_pod_publish_writes_as_the_person(self, monkeypatch, operation):
        """Named individually because these four are the commit's authorship.

        All four are `installation_ok`, so nothing about the operation stops
        the app from making them -- only the caller not having asked to be it.
        """
        monkeypatch.setattr(github_presenter, "installation_token", _explodes())
        request = _request("installation_ok", act_as="user", name=operation)

        assert await GitHubCredentialPresenter().present(request) == USER_TOKEN

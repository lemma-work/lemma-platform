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


def _request(token_kind: str | None, *, installation: str | None = "158040062"):
    return ExecutionRequest(
        connector_id="github",
        kind=ConnectorKind.HTTP,
        operation=ResolvedOperation(
            name="issues_create",
            execution={"github_token_kind": token_kind} if token_kind else {},
        ),
        payload={},
        credentials=dict(USER_TOKEN),
        config={},
        deadline_seconds=45.0,
        # The account's binding, not the install's -- an App installed on two
        # organizations gives their accounts different installations.
        account_external_ref=installation,
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

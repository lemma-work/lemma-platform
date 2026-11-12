"""What the pod's `github` connector says decides what a person may open a chat in.

The connector's answer and GitHub's are both collaborators handed to
``GithubProjectRepositories``, so these watch its own decisions: which refusal
a person sees, and what is stored when the repository is covered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.modules.agent.domain.errors import ProjectRepositoryUnavailableError
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.infrastructure.github_project_repositories import (
    GithubProjectRepositories,
)
from app.modules.agent.infrastructure.tools.workspace_cli.github_credential import (
    GithubRefusal,
    _GithubCredential,
)
from app.modules.connectors.contracts.github import GithubInstallationRequiredError

_POD = uuid4()
_PERSON = uuid4()


def _uow_factory():
    @asynccontextmanager
    async def _scope() -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(session=None)

    return _scope


async def _person(uow, user_id, pod_id):
    del uow
    return SimpleNamespace(user_id=user_id, pod_id=pod_id)


def _connector(answer: _GithubCredential | GithubRefusal | None):
    async def _credential_as(uow, ctx, *, pod_id, user_id):
        del uow
        # Resolved for the person opening the conversation, in this pod.
        assert (ctx.user_id, ctx.pod_id) == (user_id, pod_id) == (_PERSON, _POD)
        return answer

    return _credential_as


def _github_installation() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "installations": [
                {
                    "id": 7,
                    "account": {"login": "acme", "type": "Organization"},
                    "repository_selection": "all",
                }
            ]
        },
    )


def _github() -> httpx.AsyncClient:
    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/"):
            return httpx.Response(
                200, json={"owner": {"login": "acme"}, "name": "web", "private": True}
            )
        return _github_installation()

    return httpx.AsyncClient(transport=httpx.MockTransport(_answer))


def _held(*installations: str):
    """The installations ``connector_installations`` binds to the organization."""

    async def _organization_installations(uow, ctx):
        del uow
        assert (ctx.user_id, ctx.pod_id) == (_PERSON, _POD)
        return installations

    return _organization_installations


def _repositories(
    answer,
    client: httpx.AsyncClient | None = None,
    *,
    held: tuple[str, ...] = ("7",),
):
    return GithubProjectRepositories(
        _uow_factory(),
        person_context=_person,
        credential_as=_connector(answer),
        organization_installations=_held(*held),
        client=client,
    )


@pytest.mark.asyncio
async def test_a_covered_repository_is_kept_as_github_names_it():
    credential = _GithubCredential(
        access_token="ghu_person", login="octo", email=None, installation_id="7"
    )
    async with _github() as client:
        checked = await _repositories(credential, client).checked(
            ProjectRepo(owner="Acme", repo="Web", ref="release/2.0"),
            pod_id=_POD,
            user_id=_PERSON,
        )

    assert checked == ProjectRepo(owner="acme", repo="web", ref="release/2.0")
    assert checked.clone_dir.endswith("/repos/acme/web")


@pytest.mark.asyncio
async def test_a_name_github_allows_and_a_checkout_cannot_take_is_said_as_such():
    """`.github` is a real repository name, and not a directory pod files allow.
    It is the name that is refused, not GitHub reported as failing."""
    credential = _GithubCredential(
        access_token="ghu_person", login="octo", email=None, installation_id="7"
    )

    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/repos/"):
            return httpx.Response(
                200,
                json={"owner": {"login": "acme"}, "name": ".github", "private": True},
            )
        return _github_installation()

    async with httpx.AsyncClient(transport=httpx.MockTransport(_answer)) as client:
        with pytest.raises(ProjectRepositoryUnavailableError) as refused:
            await _repositories(credential, client).checked(
                ProjectRepo(owner="acme", repo="web"), pod_id=_POD, user_id=_PERSON
            )

    assert (refused.value.code, refused.value.status_code) == (
        "REPOSITORY_NAME_UNSUPPORTED",
        422,
    )
    assert "'acme/.github'" in refused.value.message


@pytest.mark.asyncio
async def test_the_connectors_refusal_reaches_the_person_with_its_remedy():
    refusal = GithubRefusal(
        code="CONNECTION_REQUIRED",
        message="Connect your github account to use 'github' in this pod.",
        connect_url="https://app.example/connect/github",
        status_code=409,
    )

    with pytest.raises(ProjectRepositoryUnavailableError) as refused:
        await _repositories(refusal).checked(
            ProjectRepo(owner="acme", repo="web"), pod_id=_POD, user_id=_PERSON
        )

    assert refused.value.code == "CONNECTION_REQUIRED"
    assert refused.value.status_code == 409
    assert refused.value.details == {
        "connect_url": "https://app.example/connect/github"
    }
    assert refused.value.message.endswith(refusal.message)


@pytest.mark.asyncio
async def test_a_shared_account_its_owner_keeps_out_of_sandboxes_is_refused():
    refusal = GithubRefusal(
        code="SHARED_ACCOUNT_NOT_FOR_SANDBOX", message="not here", status_code=403
    )

    with pytest.raises(ProjectRepositoryUnavailableError) as refused:
        await _repositories(refusal).reachable(pod_id=_POD, user_id=_PERSON)

    assert (refused.value.code, refused.value.status_code) == (
        "SHARED_ACCOUNT_NOT_FOR_SANDBOX",
        403,
    )
    assert refused.value.details is None


@pytest.mark.asyncio
async def test_an_account_with_no_token_is_asked_to_reconnect():
    with pytest.raises(ProjectRepositoryUnavailableError) as refused:
        await _repositories(None).checked(
            ProjectRepo(owner="acme", repo="web"), pod_id=_POD, user_id=_PERSON
        )

    assert (refused.value.code, refused.value.status_code) == (
        "GITHUB_TOKEN_MISSING",
        409,
    )


@pytest.mark.asyncio
async def test_an_organization_holding_no_installation_is_asked_to_install():
    credential = _GithubCredential(access_token="ghu_person", login=None, email=None)

    with pytest.raises(GithubInstallationRequiredError) as refused:
        await _repositories(credential, held=()).reachable(pod_id=_POD, user_id=_PERSON)

    assert refused.value.status_code == 409


@pytest.mark.asyncio
async def test_an_accounts_installation_the_organization_does_not_hold_reaches_nothing():
    """The account names it, and the organization never bound it: not a way in."""
    credential = _GithubCredential(
        access_token="ghu_person", login="octo", email=None, installation_id="9"
    )

    with pytest.raises(GithubInstallationRequiredError):
        await _repositories(credential, held=()).reachable(pod_id=_POD, user_id=_PERSON)


@pytest.mark.asyncio
async def test_the_picker_lists_every_installation_the_organization_holds():
    """The account's own installation first, then the others it holds."""
    credential = _GithubCredential(
        access_token="ghu_person", login="octo", email=None, installation_id="8"
    )
    installations = {
        "7": {"login": "acme", "type": "Organization", "repo": "web"},
        "8": {"login": "octo", "type": "User", "repo": "dotfiles"},
    }

    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "installations": [
                        {
                            "id": int(identifier),
                            "account": {"login": one["login"], "type": one["type"]},
                            "repository_selection": "all",
                        }
                        for identifier, one in installations.items()
                    ]
                },
            )
        identifier = request.url.path.split("/")[3]
        one = installations[identifier]
        return httpx.Response(
            200,
            json={
                "repositories": [
                    {"owner": {"login": one["login"]}, "name": one["repo"]}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_answer)) as client:
        listing = await _repositories(credential, client, held=("7", "8")).reachable(
            pod_id=_POD, user_id=_PERSON
        )

    assert [choice.repo.full_name for choice in listing.choices] == [
        "octo/dotfiles",
        "acme/web",
    ]


@pytest.mark.asyncio
async def test_the_picker_lists_the_repositories_with_their_checkouts():
    credential = _GithubCredential(
        access_token="ghu_person", login="octo", email=None, installation_id="7"
    )

    def _listed(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return _github_installation()
        assert request.url.path == "/user/installations/7/repositories"
        return httpx.Response(
            200,
            json={
                "repositories": [
                    {"owner": {"login": "acme"}, "name": "web", "private": True},
                    # Not a name GitHub produces; dropped rather than listed.
                    {"owner": {"login": "acme"}, "name": ".."},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_listed)) as client:
        listing = await _repositories(credential, client).reachable(
            pod_id=_POD, user_id=_PERSON
        )

    assert [(choice.repo, choice.private) for choice in listing.choices] == [
        (ProjectRepo(owner="acme", repo="web"), True)
    ]
    assert listing.complete is True

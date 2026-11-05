"""A person opens a conversation in a GitHub repository, and it works in a checkout.

Real HTTP API and database. GitHub and the pod's `github` connector are the
``ProjectRepositories`` port, answered by a fake installation -- except in the
last test, which runs the real one against a pod that declares no `github`
connector, so the connector's own refusal travels the whole way out.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi import FastAPI

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.api.dependencies import get_project_repositories
from app.modules.agent.domain.project_repo import ProjectRepo
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tests.fakes import FakeProjectRepositories
from app.modules.connectors.contracts.github import (
    GithubRepositoryNotInstalledError,
    GithubUnavailableError,
)
from sandbox_runtime.paths import WORKSPACE_ROOT

pytestmark = [pytest.mark.e2e]

_WEB = ProjectRepo(owner="acme", repo="web")
_CHECKOUT = f"{WORKSPACE_ROOT}/repos/acme/web"


@pytest.fixture
def github(test_app: FastAPI) -> Iterator[FakeProjectRepositories]:
    installation = FakeProjectRepositories([_WEB])
    test_app.dependency_overrides[get_project_repositories] = lambda: installation
    try:
        yield installation
    finally:
        test_app.dependency_overrides.pop(get_project_repositories, None)


async def _open(client, pod_id: str, **body):
    return await client.post(f"/pods/{pod_id}/conversations", json=body)


async def _stored_repo(conversation_id: str) -> ProjectRepo | None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = await Conversations(uow).get_conversation(UUID(conversation_id))
    assert found is not None
    return found.repo


async def _conversation_count(client, pod_id: str) -> int:
    listed = await client.get(f"/pods/{pod_id}/conversations", params={"limit": 100})
    assert listed.status_code == 200, listed.text
    return len(listed.json()["items"])


@pytest.mark.asyncio
async def test_a_conversation_opened_in_a_repository_works_in_its_checkout(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    created = await _open(authenticated_client, pod_id, repository="Acme/Web")

    assert created.status_code == 201, created.text
    body = created.json()
    # Stored as the installation spells it, so the directory is the one
    # checkout every spelling of this repository shares.
    assert body["workspace_cwd"] == _CHECKOUT
    assert body["pod_cwd"] == "/me/repos/acme/web"
    assert body["metadata"]["repo"] == {"owner": "acme", "repo": "web"}
    assert await _stored_repo(body["id"]) == _WEB
    assert github.asked == ["Acme/Web"]


@pytest.mark.asyncio
async def test_a_conversation_without_a_repository_gets_a_directory_of_its_own(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    created = await _open(authenticated_client, pod_id, title="scratch")

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["workspace_cwd"].startswith(f"{WORKSPACE_ROOT}/c/")
    assert "repo" not in (body["metadata"] or {})
    assert await _stored_repo(body["id"]) is None
    # Nobody asked GitHub about a conversation that names no repository.
    assert github.asked == []


@pytest.mark.asyncio
async def test_two_conversations_in_one_repository_share_its_checkout(
    authenticated_client, fixed_test_org, github
):
    """The accepted hazard, said rather than hidden: one sandbox per person
    means one checkout per repository, and both conversations work in it."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    first = await _open(authenticated_client, pod_id, repository="acme/web")
    second = await _open(authenticated_client, pod_id, repository="acme/web")

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["workspace_cwd"] == second.json()["workspace_cwd"] == _CHECKOUT


@pytest.mark.asyncio
async def test_a_repository_the_installation_does_not_cover_is_refused(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    github.refusal = GithubRepositoryNotInstalledError(
        "acme/private-api",
        installed_on="acme",
        manage_url="https://github.com/organizations/acme/settings/installations/7",
    )

    refused = await _open(authenticated_client, pod_id, repository="acme/private-api")

    assert refused.status_code == 422, refused.text
    error = refused.json()
    assert error["code"] == "GITHUB_REPOSITORY_NOT_INSTALLED"
    assert error["details"]["manage_url"].endswith("/settings/installations/7")
    # Refused before anything was written.
    assert await _conversation_count(authenticated_client, pod_id) == 0


@pytest.mark.asyncio
async def test_github_not_answering_refuses_rather_than_guessing(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    github.refusal = GithubUnavailableError()

    refused = await _open(authenticated_client, pod_id, repository="acme/web")

    assert refused.status_code == 503, refused.text
    assert refused.json()["code"] == "GITHUB_UNAVAILABLE"
    assert await _conversation_count(authenticated_client, pod_id) == 0


@pytest.mark.asyncio
async def test_a_name_that_is_not_owner_slash_repo_never_reaches_github(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    refused = await _open(authenticated_client, pod_id, repository="../etc/passwd")

    assert refused.status_code == 422, refused.text
    assert github.asked == []


@pytest.mark.asyncio
async def test_the_picker_lists_what_opening_accepts(
    authenticated_client, fixed_test_org, github
):
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/repositories"
    )

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [
        {
            "full_name": "acme/web",
            "owner": "acme",
            "name": "web",
            "private": True,
            "default_branch": None,
            "workspace_cwd": _CHECKOUT,
        }
    ]


@pytest.mark.asyncio
async def test_a_pod_without_a_github_connector_says_so_when_one_is_named(
    authenticated_client, fixed_test_org
):
    """No fake: the real check resolves the pod's `github` connector for the
    person, and the connector's own refusal is what they read."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)

    refused = await _open(authenticated_client, pod_id, repository="acme/web")

    assert refused.status_code == 404, refused.text
    error = refused.json()
    assert error["code"] == "POD_CONNECTOR_NOT_FOUND"
    assert "cannot be opened in a GitHub repository here" in error["message"]
    assert await _conversation_count(authenticated_client, pod_id) == 0

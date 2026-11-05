"""Which repositories an organization's GitHub installation covers, for one person.

GitHub is a `MockTransport` handed to the functions as their client, so these
exercise the real requests and the real decisions on GitHub's answers.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationUnreachableError,
    GithubRepositoryNotFoundError,
    GithubRepositoryNotInstalledError,
    GithubRepositoryUncheckedError,
    GithubUnavailableError,
)
from app.modules.connectors.services.auth.github_repositories import (
    InstallationRepository,
    RepositoryListing,
    installation_repositories,
    installation_repository,
)

_INSTALLATION = "7"
_TOKEN = "ghu_person"

Handler = Callable[[httpx.Request], httpx.Response]


def _repo(owner: str, name: str, *, private: bool = True) -> dict[str, object]:
    return {
        "owner": {"login": owner},
        "name": name,
        "private": private,
        "default_branch": "main",
    }


def _installation(
    login: str = "acme", *, selection: str = "all", kind: str = "Organization"
) -> dict[str, object]:
    return {
        "id": int(_INSTALLATION),
        "account": {"login": login, "type": kind},
        "repository_selection": selection,
    }


class _GitHub:
    """GitHub's answers by path, and the requests it was sent."""

    def __init__(
        self,
        *,
        repo: httpx.Response | None = None,
        installations: list[dict[str, object]] | None = None,
        listed: list[dict[str, object]] | None = None,
    ) -> None:
        self.repo = repo or httpx.Response(200, json=_repo("acme", "web"))
        self.installations = (
            [_installation()] if installations is None else installations
        )
        self.listed = listed or []
        self.paths: list[str] = []

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        path = request.url.path
        self.paths.append(path)
        if path.startswith("/repos/"):
            return self.repo
        if path == "/user/installations":
            return httpx.Response(200, json={"installations": self.installations})
        if path == f"/user/installations/{_INSTALLATION}/repositories":
            page = int(request.url.params.get("page", "1"))
            per_page = int(request.url.params["per_page"])
            chunk = self.listed[(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json={"repositories": chunk})
        return httpx.Response(404)


async def _covered(github: _GitHub, owner: str = "acme", name: str = "web"):
    async with github.client() as client:
        return await installation_repository(
            _TOKEN, _INSTALLATION, owner=owner, name=name, client=client
        )


@pytest.mark.asyncio
async def test_an_installation_on_every_repository_of_its_owner_covers_one():
    github = _GitHub()

    found = await _covered(github)

    assert found == InstallationRepository(
        owner="acme", name="web", private=True, default_branch="main"
    )
    # Nothing to page through when the installation covers them all.
    assert f"/user/installations/{_INSTALLATION}/repositories" not in github.paths


@pytest.mark.asyncio
async def test_the_repository_comes_back_as_github_spells_it():
    """Two spellings of one repository have to land in one checkout."""
    github = _GitHub(repo=httpx.Response(200, json=_repo("acme", "web")))

    found = await _covered(github, owner="Acme", name="Web")

    assert found.full_name == "acme/web"


def _public_web() -> httpx.Response:
    return httpx.Response(200, json=_repo("acme", "web", private=False))


@pytest.mark.asyncio
async def test_a_private_repository_the_token_reads_is_covered_without_a_walk():
    """An App's user token reads a private repository only where the App is
    installed, and the owner already names this installation."""
    github = _GitHub(installations=[_installation(selection="selected")])

    found = await _covered(github)

    assert found.private is True
    assert f"/user/installations/{_INSTALLATION}/repositories" not in github.paths


@pytest.mark.asyncio
async def test_a_selected_installation_covers_a_public_repository_it_lists():
    listed = [_repo("acme", f"other-{n}") for n in range(100)] + [_repo("acme", "web")]
    github = _GitHub(
        repo=_public_web(),
        installations=[_installation(selection="selected")],
        listed=listed,
    )

    found = await _covered(github)

    assert found.full_name == "acme/web"


@pytest.mark.asyncio
async def test_a_public_repository_deep_in_a_large_selection_is_still_found():
    """Past what a picker lists: a yes or no is walked further than a listing."""
    listed = [_repo("acme", f"other-{n}") for n in range(800)] + [_repo("acme", "web")]
    github = _GitHub(
        repo=_public_web(),
        installations=[_installation(selection="selected")],
        listed=listed,
    )

    found = await _covered(github)

    assert found.full_name == "acme/web"


@pytest.mark.asyncio
async def test_a_selection_too_large_to_walk_is_unchecked_not_refused():
    """Running out of pages is not GitHub saying no, so the remedy "add it to
    the installation" is not what the person is told."""
    listed = [_repo("acme", f"other-{n}") for n in range(10_000)]
    github = _GitHub(
        repo=_public_web(),
        installations=[_installation(selection="selected")],
        listed=listed,
    )

    with pytest.raises(GithubRepositoryUncheckedError) as refused:
        await _covered(github)

    assert refused.value.status_code == 422
    assert refused.value.details == {"repository": "acme/web", "installed_on": "acme"}
    # A create waits on a bounded number of requests, not on the whole list.
    pages = f"/user/installations/{_INSTALLATION}/repositories"
    assert github.paths.count(pages) == 10


@pytest.mark.asyncio
async def test_a_selected_installation_refuses_a_repository_it_does_not_list():
    github = _GitHub(
        repo=_public_web(),
        installations=[_installation(selection="selected")],
        listed=[_repo("acme", "docs")],
    )

    with pytest.raises(GithubRepositoryNotInstalledError) as refused:
        await _covered(github)

    assert refused.value.status_code == 422
    assert refused.value.details == {
        "repository": "acme/web",
        "installed_on": "acme",
        "manage_url": (
            "https://github.com/organizations/acme/settings/installations/7"
        ),
    }
    assert "Add it to the installation" in refused.value.message


@pytest.mark.asyncio
async def test_another_accounts_repository_is_not_this_installations():
    """A user token reads any public repository, and other installations' too."""
    github = _GitHub(
        repo=httpx.Response(200, json=_repo("someone-else", "web", private=False))
    )

    with pytest.raises(GithubRepositoryNotInstalledError) as refused:
        await _covered(github, owner="someone-else")

    assert refused.value.code == "GITHUB_REPOSITORY_NOT_INSTALLED"
    assert "installation is on 'acme'" in refused.value.message


@pytest.mark.asyncio
async def test_a_repository_github_will_not_show_is_not_found():
    github = _GitHub(repo=httpx.Response(404, json={"message": "Not Found"}))

    with pytest.raises(GithubRepositoryNotFoundError) as refused:
        await _covered(github)

    assert refused.value.status_code == 422
    assert refused.value.details == {"repository": "acme/web"}


@pytest.mark.asyncio
async def test_an_exhausted_rate_limit_is_github_not_answering():
    github = _GitHub(
        repo=httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
    )

    with pytest.raises(GithubUnavailableError) as refused:
        await _covered(github)

    assert refused.value.status_code == 503


@pytest.mark.asyncio
async def test_an_installation_the_token_no_longer_sees_is_unreachable():
    github = _GitHub(installations=[])

    with pytest.raises(GithubInstallationUnreachableError) as refused:
        await _covered(github)

    assert refused.value.status_code == 409


@pytest.mark.asyncio
async def test_a_refused_token_is_reconnected_not_retried():
    github = _GitHub(repo=httpx.Response(401, json={"message": "Bad credentials"}))

    with pytest.raises(GithubInstallationUnreachableError):
        await _covered(github)


@pytest.mark.asyncio
async def test_github_failing_to_answer_decides_nothing():
    def _down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_down)) as client:
        with pytest.raises(GithubUnavailableError):
            await installation_repository(
                _TOKEN, _INSTALLATION, owner="acme", name="web", client=client
            )


@pytest.mark.asyncio
async def test_listing_pages_through_the_installation_and_stops_at_its_bound():
    listed = [_repo("acme", f"repo-{n}") for n in range(1000)]
    github = _GitHub(listed=listed)

    async with github.client() as client:
        listing = await installation_repositories(_TOKEN, _INSTALLATION, client=client)

    # Five pages of a hundred, then it stops rather than walking all of them,
    # and says it did.
    assert len(listing.repositories) == 500
    assert listing.complete is False
    assert listing.repositories[0] == InstallationRepository(
        owner="acme", name="repo-0", private=True, default_branch="main"
    )


@pytest.mark.asyncio
async def test_listing_a_small_installation_is_complete():
    github = _GitHub(listed=[_repo("acme", "web"), _repo("acme", "docs")])

    async with github.client() as client:
        listing = await installation_repositories(_TOKEN, _INSTALLATION, client=client)

    assert listing == RepositoryListing(
        repositories=(
            InstallationRepository("acme", "web", True, "main"),
            InstallationRepository("acme", "docs", True, "main"),
        ),
        complete=True,
    )


@pytest.mark.asyncio
async def test_listing_an_installation_the_token_cannot_see_is_unreachable():
    def _gone(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_gone)) as client:
        with pytest.raises(GithubInstallationUnreachableError):
            await installation_repositories(_TOKEN, _INSTALLATION, client=client)

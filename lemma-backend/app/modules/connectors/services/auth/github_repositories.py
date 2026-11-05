"""Which repositories an organization's GitHub installation covers, for one person.

Asked with that person's own user token, never as the App: GitHub then answers
with the repositories the installation covers *and* the person can see, which
is exactly what their sandbox can clone with the same token. The enumeration is
the one ``github_installation`` describes -- a user token has no `/user/repos`,
only `/user/installations` and the repositories under one.

Every failure comes back as one of the refusals in
``domain/github_installation_errors``, never as a raw `httpx` error: the caller
is an API answering a person, and "GitHub did not answer" and "that repository
is not installed" need different words.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.log.log import get_logger
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationUnreachableError,
    GithubRepositoryNotFoundError,
    GithubRepositoryNotInstalledError,
    GithubUnavailableError,
)
from app.modules.connectors.services.auth.github_installation import (
    _API_ROOT,
    _PER_PAGE,
    _TIMEOUT,
    _USER_INSTALLATIONS_URL,
    _choice,
    _headers,
    _listed,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InstallationRepository:
    """A repository an installation covers, named the way GitHub names it."""

    owner: str
    name: str
    private: bool = False
    default_branch: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


#: Pages of an installation's repositories read before giving up. A
#: selected-repositories installation fits on the first; one covering every
#: repository of a large organization is where this bites, and a picker shows
#: the first few hundred rather than walking thousands on a page load.
_MAX_REPOSITORY_PAGES = 5


@asynccontextmanager
async def _http(client: httpx.AsyncClient | None) -> AsyncIterator[httpx.AsyncClient]:
    if client is not None:
        yield client
        return
    async with httpx.AsyncClient(timeout=_TIMEOUT) as owned:
        yield owned


def _repository(item: object) -> InstallationRepository | None:
    if not isinstance(item, dict):
        return None
    owner = item.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    name = item.get("name")
    if not login or not name:
        return None
    branch = item.get("default_branch")
    return InstallationRepository(
        owner=str(login),
        name=str(name),
        private=bool(item.get("private")),
        default_branch=str(branch) if branch else None,
    )


def _rate_limited(response: httpx.Response) -> bool:
    """GitHub answers an exhausted rate limit with a 403 too."""
    return response.status_code == 429 or (
        response.status_code == 403
        and response.headers.get("x-ratelimit-remaining") == "0"
    )


def _refused(response: httpx.Response) -> bool:
    """Whether GitHub said "not for you", as opposed to failing to answer."""
    return response.status_code in {403, 404} and not _rate_limited(response)


async def _repository_pages(
    client: httpx.AsyncClient, access_token: str, installation_id: str
) -> AsyncIterator[list[InstallationRepository]]:
    url = (
        f"{_USER_INSTALLATIONS_URL}/{quote(str(installation_id), safe='')}/repositories"
    )
    for page in range(1, _MAX_REPOSITORY_PAGES + 1):
        response = await client.get(
            url,
            headers=_headers(access_token),
            params={"per_page": _PER_PAGE, "page": page},
        )
        if _refused(response):
            raise GithubInstallationUnreachableError()
        response.raise_for_status()
        payload = response.json()
        found = payload.get("repositories") if isinstance(payload, dict) else None
        items = found if isinstance(found, list) else []
        yield [repository for repository in map(_repository, items) if repository]
        if len(items) < _PER_PAGE:
            return


@asynccontextmanager
async def _asking_github() -> AsyncIterator[None]:
    """GitHub's failures, said as this module's refusals.

    A 401 is the token itself being refused -- revoked, or the app's
    authorization withdrawn -- which reconnecting fixes. Anything else that is
    not an answer is GitHub not answering, and nothing was decided.
    """
    try:
        yield
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise GithubInstallationUnreachableError() from exc
        logger.warning(
            "connectors.github_installation.repository_lookup_failed.degraded",
            status_code=exc.response.status_code,
        )
        raise GithubUnavailableError() from exc
    except httpx.HTTPError, ValueError:
        logger.warning(
            "connectors.github_installation.repository_lookup_failed.degraded",
            exc_info=True,
        )
        raise GithubUnavailableError() from None


async def installation_repositories(
    access_token: str,
    installation_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[InstallationRepository, ...]:
    """The repositories this token's person reaches through one installation.

    Asked as the person, not as the App: GitHub answers with the repositories
    the installation covers *and* the person can see, which is exactly what
    their sandbox can clone.
    """
    async with _asking_github(), _http(client) as http:
        return tuple(
            [
                repository
                async for page in _repository_pages(http, access_token, installation_id)
                for repository in page
            ]
        )


async def installation_repository(
    access_token: str,
    installation_id: str,
    *,
    owner: str,
    name: str,
    client: httpx.AsyncClient | None = None,
) -> InstallationRepository:
    """``owner/name`` as GitHub names it, if this installation covers it.

    The installation decides, not the token alone. A user token reaches every
    installation of the App its person can see -- another Lemma organization's
    included -- and any public repository at all, so "the token can read it"
    would accept repositories this organization never installed the app on.
    An installation lives on exactly one GitHub account, so the repository's
    owner has to be that account; one installed on *all* of that account's
    repositories then covers it, and one installed on a selection has to list
    it.

    Returned with GitHub's own spelling: a renamed repository answers under
    its new name, and ``Acme/Web`` comes back as ``acme/web``, so two
    conversations naming one repository two ways share one checkout rather
    than cloning it twice.

    Raises the refusals in ``domain/github_installation_errors``.
    """
    async with _asking_github(), _http(client) as http:
        response = await http.get(
            f"{_API_ROOT}/repos/{quote(owner, safe='')}/{quote(name, safe='')}",
            headers=_headers(access_token),
            follow_redirects=True,
        )
        if _refused(response):
            raise GithubRepositoryNotFoundError(f"{owner}/{name}")
        response.raise_for_status()
        repository = _repository(response.json())
        if repository is None:
            raise GithubUnavailableError()

        listed = await _listed(http, access_token, installation_id)
        choice = _choice(listed) if listed else None
        if choice is None:
            raise GithubInstallationUnreachableError()
        not_installed = GithubRepositoryNotInstalledError(
            repository.full_name,
            installed_on=choice.account_login,
            manage_url=choice.manage_url,
        )
        if (choice.account_login or "").lower() != repository.owner.lower():
            raise not_installed
        if choice.covers_every_repository:
            return repository
        wanted = repository.full_name.lower()
        async for page in _repository_pages(http, access_token, installation_id):
            if any(covered.full_name.lower() == wanted for covered in page):
                return repository
        raise not_installed

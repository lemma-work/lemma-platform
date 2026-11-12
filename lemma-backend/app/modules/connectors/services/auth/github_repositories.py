"""Which repositories an organization's GitHub installations cover, for one person.

Asked with that person's own user token, never as the App: GitHub then answers
with the repositories an installation covers *and* the person can see, which
is exactly what their sandbox can clone with the same token. The enumeration is
the one ``github_installation`` describes -- a user token has no `/user/repos`,
only `/user/installations` and the repositories under one.

An organization may hold several installations -- someone's own GitHub account
and the GitHub organization they work in, each connected through an account of
its own -- and the caller names which (``github_bindings.bound_installations``).
The token reaches others too, another Lemma organization's included; only the
ones named are read.

Every failure comes back as one of the refusals in
``domain/github_installation_errors``, never as a raw `httpx` error: the caller
is an API answering a person, and "GitHub did not answer" and "that repository
is not installed" need different words.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.log.log import get_logger
from app.modules.connectors.domain.github_installation_errors import (
    GithubInstallationUnreachableError,
    GithubRepositoryNotFoundError,
    GithubRepositoryNotInstalledError,
    GithubRepositoryUncheckedError,
    GithubUnavailableError,
)
from app.modules.connectors.services.auth.github_installation import (
    _API_ROOT,
    _MAX_INSTALLATION_PAGES,
    _PER_PAGE,
    _TIMEOUT,
    _USER_INSTALLATIONS_URL,
    InstallationChoice,
    _choice,
    _headers,
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


@dataclass(frozen=True, slots=True)
class RepositoryListing:
    """An installation's repositories, as far as they were read."""

    repositories: tuple[InstallationRepository, ...]
    #: False when GitHub had more than the walk was allowed to read.
    complete: bool = True


#: Pages a picker reads of each installation. A selected-repositories installation fits on the
#: first; one covering every repository of a large organization is where this
#: bites, and a picker shows the first few hundred rather than walking
#: thousands on a page load -- saying so with ``complete``.
_MAX_LISTING_PAGES = 5
#: Pages read to decide one public repository on a selection: past what a
#: picker shows, so a yes or no is right for any selection a person curates by
#: hand, and short enough that a create naming a repository the selection lacks
#: answers in a few requests. Running out is its own refusal, never read as
#: "not installed".
_MAX_COVERAGE_PAGES = 10


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
    client: httpx.AsyncClient,
    access_token: str,
    installation_id: str,
    *,
    max_pages: int,
) -> AsyncIterator[tuple[list[InstallationRepository], bool]]:
    """Each page of the installation's repositories, and whether more follow.

    GitHub states how many there are (`total_count`), so a list that ends
    exactly on a full page is known to end there. A last page that still says
    "more" means the walk stopped at ``max_pages`` with repositories unread.
    A page without the count falls back to "a full page may have more".
    """
    url = (
        f"{_USER_INSTALLATIONS_URL}/{quote(str(installation_id), safe='')}/repositories"
    )
    for page in range(1, max_pages + 1):
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
        total = payload.get("total_count") if isinstance(payload, dict) else None
        more = (
            total > page * _PER_PAGE
            if isinstance(total, int)
            else len(items) >= _PER_PAGE
        )
        yield [repository for repository in map(_repository, items) if repository], more
        if not more:
            return


async def _reachable(
    client: httpx.AsyncClient, access_token: str, installation_ids: Sequence[str]
) -> list[InstallationChoice]:
    """The named installations this token's person can see, in the order named.

    One read of `/user/installations` rather than one per installation. One
    the person cannot see -- a GitHub organization they are not in, an app
    since uninstalled -- is left out; none at all is refused, since nothing
    can be cloned then.
    """
    wanted = [str(one) for one in installation_ids]
    seen: dict[str, InstallationChoice] = {}
    for page in range(1, _MAX_INSTALLATION_PAGES + 1):
        response = await client.get(
            _USER_INSTALLATIONS_URL,
            headers=_headers(access_token),
            params={"per_page": _PER_PAGE, "page": page},
        )
        if _refused(response):
            raise GithubInstallationUnreachableError()
        response.raise_for_status()
        payload = response.json()
        found = payload.get("installations") if isinstance(payload, dict) else None
        items = [item for item in (found or []) if isinstance(item, dict)]
        for item in items:
            choice = _choice(item)
            if choice is not None and choice.installation_id in wanted:
                seen[choice.installation_id] = choice
        if len(items) < _PER_PAGE or len(seen) == len(set(wanted)):
            break
    reachable = [seen[one] for one in dict.fromkeys(wanted) if one in seen]
    if not reachable:
        raise GithubInstallationUnreachableError()
    return reachable


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
    installation_ids: Sequence[str],
    *,
    client: httpx.AsyncClient | None = None,
) -> RepositoryListing:
    """The repositories this token's person reaches through these installations.

    Asked as the person, not as the App: GitHub answers with the repositories
    each installation covers *and* the person can see, which is exactly what
    their sandbox can clone. Installation by installation, in the order
    named, at most a picker's worth of each; ``complete`` says whether that
    was all of them.
    """
    repositories: list[InstallationRepository] = []
    complete = True
    async with _asking_github(), _http(client) as http:
        for choice in await _reachable(http, access_token, installation_ids):
            more = False
            async for page, more in _repository_pages(
                http,
                access_token,
                choice.installation_id,
                max_pages=_MAX_LISTING_PAGES,
            ):
                repositories.extend(page)
            complete = complete and not more
    # Two installations never share an owner, so a repository is listed once;
    # the guard is for GitHub answering a page twice while it changes.
    unique = {repository.full_name.lower(): repository for repository in repositories}
    return RepositoryListing(repositories=tuple(unique.values()), complete=complete)


async def installation_repository(
    access_token: str,
    installation_ids: Sequence[str],
    *,
    owner: str,
    name: str,
    client: httpx.AsyncClient | None = None,
) -> InstallationRepository:
    """``owner/name`` as GitHub names it, if one of these installations covers it.

    The installations decide, not the token alone. A user token reaches every
    installation of the App its person can see -- another Lemma organization's
    included -- and any public repository at all, so "the token can read it"
    would accept repositories this organization never installed the app on.
    An installation lives on exactly one GitHub account, so the one that can
    cover the repository is the one on its owner. Then any of three things
    proves it covered:
    the installation is on *all* that account's repositories; the repository
    is private, which an App's user token can read only where the App is
    installed; or the installation's selection lists it.

    Returned with GitHub's own spelling: a renamed repository answers under
    its new name, and ``Acme/Web`` comes back as ``acme/web``, so two
    conversations naming one repository two ways share one clone rather than
    cloning it twice.

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

        reachable = await _reachable(http, access_token, installation_ids)
        owner = repository.owner.lower()
        choice = next(
            (one for one in reachable if (one.account_login or "").lower() == owner),
            None,
        )
        if choice is None:
            raise GithubRepositoryNotInstalledError(
                repository.full_name,
                installed_on=tuple(
                    one.account_login for one in reachable if one.account_login
                ),
                manage_url=reachable[0].manage_url,
            )
        not_installed = GithubRepositoryNotInstalledError(
            repository.full_name,
            installed_on=choice.account_login,
            manage_url=choice.manage_url,
        )
        if choice.covers_every_repository or repository.private:
            return repository
        wanted = repository.full_name.lower()
        more = False
        async for page, more in _repository_pages(
            http, access_token, choice.installation_id, max_pages=_MAX_COVERAGE_PAGES
        ):
            if any(covered.full_name.lower() == wanted for covered in page):
                return repository
        if more:
            raise GithubRepositoryUncheckedError(
                repository.full_name, installed_on=choice.account_login
            )
        raise not_installed

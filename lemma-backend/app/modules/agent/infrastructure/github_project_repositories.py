"""The GitHub repositories a person may open a conversation in.

A conversation opened in a repository works in a checkout of it: its directory
is the checkout (``domain/workspace_location``), and the sandbox clones it with
the pod ``github`` connector's account before the agent's first command
(``tools/workspace_cli/github_project``). So the question at opening is not
"does this repository exist" but "will that clone work" -- asked of the very
account the clone will use (``github_credential_as``), for the person opening
the conversation, and answered by the organization's installation rather than
by the token alone (``connectors.contracts.github``).

No database connection is held while GitHub is asked: the account is resolved
in a short unit of work of its own, which has closed before the first request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import httpx

from app.core.authorization.context import Context
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.scope import context_scope
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.agent.domain.errors import ProjectRepositoryUnavailableError
from app.modules.agent.domain.project_repo import (
    ProjectRepo,
    RepositoryChoice,
    RepositoryChoices,
    parse_project_repo,
)
from app.modules.agent.infrastructure.tools.workspace_cli.github_credential import (
    GithubRefusal,
    _GithubCredential,
    github_credential_as,
)
from app.modules.connectors.contracts.github import (
    GithubInstallationRequiredError,
    InstallationRepository,
    installation_repositories,
    installation_repository,
)

#: The person's own authority in the pod: who the account is resolved for.
PersonContext = Callable[[SqlAlchemyUnitOfWork, UUID, UUID], Awaitable[Context]]
#: ``github_credential_as``'s shape, so a test can answer for the connector.
CredentialAs = Callable[..., Awaitable[_GithubCredential | GithubRefusal | None]]


async def _person_context(
    uow: SqlAlchemyUnitOfWork, user_id: UUID, pod_id: UUID
) -> Context:
    return await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )


@dataclass(frozen=True, slots=True)
class _Reach:
    """A person's way into the organization's installation."""

    access_token: str
    installation_id: str


def _project_repo(
    found: InstallationRepository, *, ref: str | None = None
) -> ProjectRepo | None:
    """GitHub's name held to the rules a directory and a clone URL need.

    GitHub's own naming rules are narrower than these, so this only ever
    drops something GitHub would not have produced.
    """
    return parse_project_repo({"owner": found.owner, "repo": found.name, "ref": ref})


class GithubProjectRepositories:
    """``ProjectRepositories``, answered by GitHub through the pod's connector."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        *,
        person_context: PersonContext | None = None,
        credential_as: CredentialAs | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        # Every collaborator is an argument so a test answers for the connector
        # and for GitHub without replacing names inside this module; resolved
        # here rather than in the signature, which would bind them at import.
        self._uow_factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
        self._person_context = person_context or _person_context
        self._credential_as = credential_as or github_credential_as
        self._client = client

    async def checked(
        self, repo: ProjectRepo, *, pod_id: UUID, user_id: UUID
    ) -> ProjectRepo:
        reach = await self._reach(pod_id=pod_id, user_id=user_id)
        found = await installation_repository(
            reach.access_token,
            reach.installation_id,
            owner=repo.owner,
            name=repo.repo,
            client=self._client,
        )
        checked = _project_repo(found, ref=repo.ref)
        if checked is None:
            # GitHub's rules allow a few names a checkout directory may not
            # take -- `.github`, whose leading dot pod files reserve for
            # hidden entries. Said as the name, not as GitHub failing.
            raise ProjectRepositoryUnavailableError(
                f"GitHub names it '{found.full_name}', which cannot be used as "
                "a checkout directory.",
                code="REPOSITORY_NAME_UNSUPPORTED",
                status_code=422,
            )
        return checked

    async def reachable(self, *, pod_id: UUID, user_id: UUID) -> RepositoryChoices:
        reach = await self._reach(pod_id=pod_id, user_id=user_id)
        listing = await installation_repositories(
            reach.access_token, reach.installation_id, client=self._client
        )
        choices: list[RepositoryChoice] = []
        for repository in listing.repositories:
            repo = _project_repo(repository)
            if repo is not None:
                choices.append(
                    RepositoryChoice(
                        repo=repo,
                        private=repository.private,
                        default_branch=repository.default_branch,
                    )
                )
        return RepositoryChoices(choices=tuple(choices), complete=listing.complete)

    async def _reach(self, *, pod_id: UUID, user_id: UUID) -> _Reach:
        """The token and installation the sandbox would clone with, or why not."""
        async with self._uow_factory() as uow:
            ctx = await self._person_context(uow, user_id, pod_id)
            async with context_scope(ctx):
                credential = await self._credential_as(
                    uow, ctx, pod_id=pod_id, user_id=user_id
                )
        if isinstance(credential, GithubRefusal):
            raise ProjectRepositoryUnavailableError(
                credential.message,
                code=credential.code,
                status_code=credential.status_code,
                connect_url=credential.connect_url,
            )
        if credential is None:
            raise ProjectRepositoryUnavailableError(
                "the GitHub account this pod's 'github' connector uses holds no "
                "token. Reconnect it, then try again.",
                code="GITHUB_TOKEN_MISSING",
                status_code=409,
            )
        if not credential.installation_id:
            raise GithubInstallationRequiredError()
        return _Reach(
            access_token=credential.access_token,
            installation_id=credential.installation_id,
        )

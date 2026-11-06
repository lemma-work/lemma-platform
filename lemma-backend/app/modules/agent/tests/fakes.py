"""Fakes for the agent module's ports."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.domain.errors import DomainError
from app.modules.agent.domain.ports import ProjectRepositories
from app.modules.agent.domain.project_repo import (
    ProjectRepo,
    RepositoryChoice,
    RepositoryChoices,
)


class FakeProjectRepositories(ProjectRepositories):
    """GitHub, as one organization's installation: the repositories it covers.

    Covered names are matched the way GitHub matches them, ignoring case, and
    answered with the covered spelling. ``refusal`` stands in for anything
    GitHub or the pod's connector would refuse with.
    """

    def __init__(
        self,
        covered: Sequence[ProjectRepo] = (),
        *,
        refusal: DomainError | None = None,
    ) -> None:
        self.covered = list(covered)
        self.refusal = refusal
        self.asked: list[str] = []

    async def checked(
        self, repo: ProjectRepo, *, pod_id: UUID, user_id: UUID
    ) -> ProjectRepo:
        self.asked.append(repo.full_name)
        if self.refusal is not None:
            raise self.refusal
        for covered in self.covered:
            if covered.full_name.lower() == repo.full_name.lower():
                return ProjectRepo(owner=covered.owner, repo=covered.repo, ref=repo.ref)
        raise DomainError(
            f"'{repo.full_name}' is not covered",
            code="GITHUB_REPOSITORY_NOT_INSTALLED",
            status_code=422,
        )

    async def reachable(self, *, pod_id: UUID, user_id: UUID) -> RepositoryChoices:
        if self.refusal is not None:
            raise self.refusal
        return RepositoryChoices(
            choices=tuple(
                RepositoryChoice(repo=repo, private=True) for repo in self.covered
            )
        )

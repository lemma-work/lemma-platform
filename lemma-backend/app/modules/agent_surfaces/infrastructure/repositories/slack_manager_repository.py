"""The manager's install and the Slack people it has proof of.

Pending teammate apps are ``SlackPendingAppBindingRepository``'s.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.infrastructure.repositories.slack_configuration_token_repository import (  # noqa: E501
    MAX_WORKSPACES_PER_ORGANIZATION,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
    SlackWorkspacePersonModel,
)

_Install = SlackManagerInstallModel
_Person = SlackWorkspacePersonModel

#: How a Slack user was proven (``slack_workspace_people.proved_by``).
PROVED_BY_INSTALL = "INSTALL"
PROVED_BY_OIDC = "OIDC"


class WorkspaceHeldElsewhereError(AgentSurfaceValidationError):
    """A second organization binding a workspace another already holds."""

    def __init__(self) -> None:
        super().__init__(
            "This Slack workspace already belongs to another Lemma organization. "
            "Remove Lemma from it in Slack (Manage apps) to free it, then add it "
            "here again."
        )


@dataclass(frozen=True, slots=True)
class ClaimedInstall:
    install_id: UUID
    team_name: str | None
    #: The binding is new: no row for this workspace before.
    created: bool
    #: The account the binding used before this one, now superseded.
    superseded_account_id: UUID | None


class SlackManagerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # -- the install ---------------------------------------------------------

    async def install_for_team(self, team_id: str) -> SlackManagerInstallModel | None:
        result = await self.session.execute(
            select(_Install)
            .where(_Install.team_id == team_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def installs_for_organization(
        self, organization_id: UUID
    ) -> list[SlackManagerInstallModel]:
        result = await self.session.execute(
            select(_Install)
            .where(_Install.organization_id == organization_id)
            .order_by(_Install.created_at)
            .limit(MAX_WORKSPACES_PER_ORGANIZATION)
        )
        return list(result.scalars().all())

    async def claim_install(
        self,
        *,
        team_id: str,
        team_name: str | None,
        organization_id: UUID,
        account_id: UUID,
        bot_user_id: str,
        installed_by_user_id: UUID | None,
    ) -> ClaimedInstall:
        """Bind the workspace to the organization, or refresh its binding.

        One organization per workspace: a second organization is refused, not
        quietly moved, because the workspace's people already meet the first
        one there. The insert is conflict-tolerant, so two organizations
        binding at once give one binding and one refusal, never a 500.
        """
        inserted = await self.session.scalar(
            insert(_Install)
            .values(
                id=uuid4(),
                team_id=team_id,
                team_name=team_name,
                organization_id=organization_id,
                account_id=account_id,
                bot_user_id=bot_user_id,
                installed_by_user_id=installed_by_user_id,
                created_at=func.now(),
                updated_at=func.now(),
            )
            .on_conflict_do_nothing()
            .returning(_Install.id)
        )
        if inserted is not None:
            return ClaimedInstall(inserted, team_name, True, None)
        row = await self.install_for_team(team_id)
        if row is None:
            # The conflict was on the account: it already binds another
            # workspace, which one Slack grant never does.
            raise AgentSurfaceValidationError(
                "That Slack account already belongs to another workspace's Lemma."
            )
        if row.organization_id != organization_id:
            raise WorkspaceHeldElsewhereError()
        previous = row.account_id
        await self.session.execute(
            update(_Install)
            .where(_Install.id == row.id)
            .values(
                team_name=team_name or row.team_name,
                account_id=account_id,
                bot_user_id=bot_user_id,
                installed_by_user_id=installed_by_user_id or row.installed_by_user_id,
                updated_at=func.now(),
            )
        )
        return ClaimedInstall(
            row.id,
            team_name or row.team_name,
            False,
            previous if previous != account_id else None,
        )

    async def delete_install(self, team_id: str) -> bool:
        result = await self.session.execute(
            delete(_Install).where(_Install.team_id == team_id)
        )
        return bool(getattr(result, "rowcount", 0))

    # -- people --------------------------------------------------------------

    async def person(self, team_id: str, slack_user_id: str) -> UUID | None:
        """The Lemma person this Slack user is proven to be, if anyone."""
        return await self.session.scalar(
            select(_Person.user_id).where(
                _Person.team_id == team_id, _Person.slack_user_id == slack_user_id
            )
        )

    async def slack_user_for(self, team_id: str, user_id: UUID) -> str | None:
        """The Slack user this Lemma person is proven to be in the workspace."""
        return await self.session.scalar(
            select(_Person.slack_user_id).where(
                _Person.team_id == team_id, _Person.user_id == user_id
            )
        )

    async def prove_person(
        self, *, team_id: str, slack_user_id: str, user_id: UUID, proved_by: str
    ) -> list[str]:
        """Record that this Slack user is this person, and return the Slack
        users whose proof that replaces.

        One Slack user per person per workspace, both ways: the Slack user's
        older proof and the person's older Slack user both go.
        """
        replaced = list(
            (
                await self.session.scalars(
                    delete(_Person)
                    .where(
                        _Person.team_id == team_id,
                        or_(
                            _Person.slack_user_id == slack_user_id,
                            _Person.user_id == user_id,
                        ),
                    )
                    .returning(_Person.slack_user_id)
                )
            ).all()
        )
        await self.session.execute(
            insert(_Person)
            .values(
                id=uuid4(),
                team_id=team_id,
                slack_user_id=slack_user_id,
                user_id=user_id,
                proved_by=proved_by,
                created_at=func.now(),
                updated_at=func.now(),
            )
            # A proof racing this one for the same pair: either is the proof.
            .on_conflict_do_nothing()
        )
        return [other for other in replaced if other != slack_user_id]

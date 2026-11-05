"""Who the manager is talking to, and which teammates they can see.

Lemma's own app manages a workspace's teammates, and managing needs to know the
person for certain: a Slack profile email is editable by a workspace admin, so
it never stands in for a Lemma account here. The proof is Slack's own word
(``slack_workspace_people``): the installer's grant, or Sign in with Slack,
which a person starts from the link their App Home shows
(``services/slack_person_proof``).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from app.core.authorization.delegation import is_pod_default_agent
from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.credentials import CredentialsLike
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (  # noqa: E501
    SlackManagerRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.infrastructure.slack_manager_models import (
    SlackManagerInstallModel,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.group_names import visible_bot_name
from app.modules.identity.contracts.organizations import (
    organization_member_role,
    organization_name,
    preferred_organization_membership,
)
from app.modules.pod.contracts.user_pods import list_visible_pods

#: Pod roles that may set a teammate up: add it to Slack, give it a channel.
MANAGING_POD_ROLES = frozenset({"POD_ADMIN", "POD_EDITOR"})
_ORG_OWNER = "ORG_OWNER"


def sign_in_url(team_id: str, slack_user_id: str) -> str:
    """The "Connect your Lemma account" link one person's App Home shows them.

    Names whom it is for, and proves nothing by itself: the page it opens asks
    the signed-in person to sign in with Slack, and only Slack's answer --
    that same Slack user, in that workspace -- connects anything.
    """
    query = urlencode({"team": team_id, "user": slack_user_id})
    return f"{settings.frontend_url.rstrip('/')}/slack/connect?{query}"


@dataclass(frozen=True)
class Teammate:
    """One agent of one pod, as the manager offers it."""

    pod_id: UUID
    agent_id: UUID
    name: str
    #: The agent's surface in this workspace, once its own app is in.
    surface: AgentSurfaceEntity | None
    #: Whether the viewer may add it to Slack or give it a channel.
    can_manage: bool

    @property
    def bot_user_id(self) -> str | None:
        return self.surface.surface_identity_id if self.surface else None


@dataclass(frozen=True)
class ManagedInstall:
    """The install row's values, read once.

    Not the row itself: a context outlives commits -- adding a teammate commits
    midway and then republishes the Home -- and an expired ORM row read after
    one is a lazy load on a session that has moved on.
    """

    install_id: UUID
    team_id: str
    team_name: str | None
    organization_id: UUID
    account_id: UUID
    bot_user_id: str

    @classmethod
    def of(cls, row: SlackManagerInstallModel) -> ManagedInstall:
        return cls(
            install_id=row.id,
            team_id=row.team_id,
            team_name=row.team_name,
            organization_id=row.organization_id,
            account_id=row.account_id,
            bot_user_id=row.bot_user_id,
        )


@dataclass(frozen=True)
class ManagerContext:
    install: ManagedInstall
    credentials: CredentialsLike
    slack_user_id: str | None
    #: The Lemma person this Slack user is proven to be, if anyone.
    user_id: UUID | None
    organization_name: str | None
    organization_role: str | None

    @property
    def team_id(self) -> str:
        return self.install.team_id

    @property
    def organization_id(self) -> UUID:
        return self.install.organization_id

    @property
    def is_member(self) -> bool:
        return self.organization_role is not None


async def load_install(
    uow: SqlAlchemyUnitOfWork, team_id: str | None
) -> ManagedInstall | None:
    if not team_id:
        return None
    row = await SlackManagerRepository(uow.session).install_for_team(team_id)
    return ManagedInstall.of(row) if row is not None else None


async def load_context(
    uow: SqlAlchemyUnitOfWork, *, team_id: str | None, slack_user_id: str | None
) -> ManagerContext | None:
    """None when Lemma's app has no install here: nothing to manage, nobody to ask."""
    install = await load_install(uow, team_id)
    if install is None:
        return None
    credentials = await SurfaceCredentialResolver(uow=uow).for_account(
        install.account_id, platform=SurfacePlatform.SLACK
    )
    user_id = (
        await SlackManagerRepository(uow.session).person(install.team_id, slack_user_id)
        if slack_user_id
        else None
    )
    role = (
        await organization_member_role(
            uow, user_id=user_id, organization_id=install.organization_id
        )
        if user_id
        else None
    )
    return ManagerContext(
        install=install,
        credentials=credentials,
        slack_user_id=slack_user_id,
        user_id=user_id,
        organization_name=await organization_name(uow, install.organization_id),
        organization_role=role.value if role is not None else None,
    )


async def organization_pod_ids(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID
) -> list[UUID]:
    """Every pod in the organization: asked as its owner would see them."""
    pods = await list_visible_pods(
        session=uow.session,
        organization_ids=[organization_id],
        organization_member_ids_by_org={},
        owned_organization_ids={organization_id},
    )
    return [pod.id for pod in pods]


async def workspace_surfaces(
    uow: SqlAlchemyUnitOfWork, team_id: str, *, pod_ids: list[UUID]
) -> list[AgentSurfaceEntity]:
    """These pods' own apps in this workspace. Scoped by pod, because a workspace
    is one organization's and a surface carries its pod, not its organization."""
    if not pod_ids:
        return []
    surfaces = await SurfaceRepository(uow).list_active_for_routing(
        SurfacePlatform.SLACK.value,
        external_workspace_id=team_id,
        pod_ids=pod_ids,
    )
    return [surface for surface in surfaces if surface.surface_identity_id]


def _agent_of(surface: AgentSurfaceEntity) -> UUID:
    """The surface's agent; the pod's own agent's id is the pod's."""
    agent_id = surface.agent_id
    if agent_id is None or is_pod_default_agent(agent_id, pod_id=surface.pod_id):
        return surface.pod_id
    return agent_id


async def teammates(uow: SqlAlchemyUnitOfWork, ctx: ManagerContext) -> list[Teammate]:
    """Every teammate the viewer can see in the organization, and its Slack app.

    One row per agent with an app in this workspace -- a pod may have several,
    each its own app -- and the pod's own agent for every pod, so a pod
    nobody has added yet offers its "Add to Slack".
    """
    if ctx.user_id is None or not ctx.is_member:
        return []
    membership = await preferred_organization_membership(
        uow, user_id=ctx.user_id, preferred_organization_id=ctx.organization_id
    )
    if membership is None or membership[0] != ctx.organization_id:
        return []
    owner = ctx.organization_role == _ORG_OWNER
    pods = await list_visible_pods(
        session=uow.session,
        organization_ids=[ctx.organization_id],
        organization_member_ids_by_org={ctx.organization_id: membership[1]},
        owned_organization_ids={ctx.organization_id} if owner else set(),
    )
    surfaces = await workspace_surfaces(
        uow, ctx.team_id, pod_ids=[pod.id for pod in pods]
    )
    by_agent: dict[tuple[UUID, UUID], AgentSurfaceEntity] = {}
    for surface in surfaces:
        by_agent.setdefault((surface.pod_id, _agent_of(surface)), surface)
    mates: list[Teammate] = []
    for pod in sorted(pods, key=lambda pod: pod.name.lower()):
        can_manage = owner or bool(MANAGING_POD_ROLES & set(pod.roles))
        own = by_agent.pop((pod.id, pod.id), None)
        mates.append(
            Teammate(
                pod_id=pod.id,
                agent_id=pod.id,
                name=await visible_bot_name(uow, own) if own else pod.name,
                surface=own,
                can_manage=can_manage,
            )
        )
        for (pod_id, agent_id), surface in sorted(
            ((key, value) for key, value in by_agent.items() if key[0] == pod.id),
            key=lambda item: str(item[0][1]),
        ):
            mates.append(
                Teammate(
                    pod_id=pod_id,
                    agent_id=agent_id,
                    name=await visible_bot_name(uow, surface),
                    surface=surface,
                    can_manage=can_manage,
                )
            )
    return mates

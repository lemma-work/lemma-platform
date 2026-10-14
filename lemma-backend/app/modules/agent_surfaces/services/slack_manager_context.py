"""Who the manager is talking to, and which teammates they can see.

Lemma's own app manages a workspace's teammates, and managing needs to know the
person for certain: a Slack profile email is editable by a workspace admin, so
it never stands in for a Lemma account here. The proof is a link only that
person's own App Home shows -- they open it, sign in to Lemma, and from then on
this Slack user is them (``slack_workspace_people``). Installing Lemma proves
the installer the same way, through the OAuth grant that names them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from app.core.config import settings
from app.core.crypto import get_secret_signer
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.credentials import CredentialsLike
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
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
from app.modules.identity.contracts.organizations import (
    organization_member_role,
    organization_name,
    preferred_organization_membership,
)
from app.modules.pod.contracts.user_pods import list_visible_pods

LINK_PURPOSE = "slack-workspace-person"
LINK_TTL_SECONDS = 15 * 60
#: Pod roles that may set a teammate up: add it to Slack, give it a channel.
MANAGING_POD_ROLES = frozenset({"POD_ADMIN", "POD_EDITOR"})
_ORG_OWNER = "ORG_OWNER"


def _link_payload(team_id: str, slack_user_id: str, expires: int) -> bytes:
    return f"{team_id}:{slack_user_id}:{expires}".encode()


def person_link_url(
    team_id: str, slack_user_id: str, *, now: float | None = None
) -> str:
    """The "Connect your Lemma account" link one person's App Home shows them."""
    expires = int((now or time.time()) + LINK_TTL_SECONDS)
    signature = get_secret_signer().sign(
        LINK_PURPOSE, _link_payload(team_id, slack_user_id, expires)
    )
    query = urlencode(
        {"team": team_id, "user": slack_user_id, "exp": expires, "sig": signature}
    )
    return f"{settings.frontend_url.rstrip('/')}/slack/connect?{query}"


def verify_person_link(
    *,
    team_id: str,
    slack_user_id: str,
    expires: int,
    signature: str,
    now: float | None = None,
) -> bool:
    if expires < (now or time.time()):
        return False
    return get_secret_signer().verify(
        LINK_PURPOSE, _link_payload(team_id, slack_user_id, expires), signature
    )


def teammate_name(pod_name: str) -> str:
    """The pod's name as a teammate's: what the web app shows, and the bot is called."""
    words = " ".join(pod_name.replace("-", " ").replace("_", " ").split())
    return words[:1].upper() + words[1:] if words else pod_name


@dataclass(frozen=True)
class Teammate:
    pod_id: UUID
    name: str
    #: The pod's surface in this workspace, once its own app is in.
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

    team_id: str
    team_name: str | None
    organization_id: UUID
    account_id: UUID
    bot_user_id: str

    @classmethod
    def of(cls, row: SlackManagerInstallModel) -> ManagedInstall:
        return cls(
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


async def load_context(
    uow: SqlAlchemyUnitOfWork, *, team_id: str | None, slack_user_id: str | None
) -> ManagerContext | None:
    """None when Lemma's app has no install here: nothing to manage, nobody to ask."""
    if not team_id:
        return None
    row = await SlackManagerRepository(uow.session).install_for_team(team_id)
    if row is None:
        return None
    install = ManagedInstall.of(row)
    credentials = await SurfaceCredentialResolver(uow=uow).for_account(
        install.account_id, platform=SurfacePlatform.SLACK
    )
    user_id = (
        await SlackManagerRepository(uow.session).person(team_id, slack_user_id)
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
    uow: SqlAlchemyUnitOfWork, ctx: ManagerContext, *, pod_ids: list[UUID]
) -> list[AgentSurfaceEntity]:
    """These pods' own apps in this workspace. Scoped by pod, because a workspace
    is one organization's and a surface carries its pod, not its organization."""
    if not pod_ids:
        return []
    surfaces = await SurfaceRepository(uow).list_active_for_routing(
        SurfacePlatform.SLACK.value,
        external_workspace_id=ctx.team_id,
        pod_ids=pod_ids,
    )
    return [surface for surface in surfaces if surface.surface_identity_id]


async def teammates(uow: SqlAlchemyUnitOfWork, ctx: ManagerContext) -> list[Teammate]:
    """Every teammate the viewer can see in the organization, and its Slack app."""
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
    by_pod = {
        surface.pod_id: surface
        for surface in await workspace_surfaces(
            uow, ctx, pod_ids=[pod.id for pod in pods]
        )
    }
    return [
        Teammate(
            pod_id=pod.id,
            name=teammate_name(pod.name),
            surface=by_pod.get(pod.id),
            can_manage=owner or bool(MANAGING_POD_ROLES & set(pod.roles)),
        )
        for pod in sorted(pods, key=lambda pod: pod.name.lower())
    ]

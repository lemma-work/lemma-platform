"""The people of a Slack workspace Lemma's own app manages, as a teammate sees them.

A workspace bound to an organization is that organization's: everyone in it is
somebody a teammate there answers. Members route as themselves. Everyone else
is a contact -- known by their Slack id, vouched for by Slack's signature on
the event, never a seat -- in a private chat, and an outsider in a channel,
answered as the channel's owner would let the pod answer the public.

Slack's guests are the exception: let into the workspace from outside it, they
are outsiders everywhere, as a Slack Connect colleague from another company is.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_config import (
    ContactAnswer,
    SurfaceContactPolicy,
)

#: Whether Lemma's own app manages this Slack workspace (by team id).
ManagedWorkspaceCheck = Callable[[str], Awaitable[bool]]


async def default_workspace_contacts(
    entity: AgentSurfaceEntity,
    *,
    owner_user_id: UUID | None,
    is_managed: ManagedWorkspaceCheck | None,
) -> None:
    """A teammate's own Slack bot in a managed workspace answers its people.

    Only a policy nobody has set is filled in: answering off, looked after by
    no one. Whoever connected the bot looks after them, as turning contacts on
    by hand would make them.
    """
    policy = entity.config.contacts
    if (
        entity.surface_type is not SurfacePlatform.SLACK
        or is_managed is None
        or owner_user_id is None
        or not entity.external_workspace_id
        or policy.answer is not ContactAnswer.OFF
        or policy.looked_after_by is not None
    ):
        return
    if await is_managed(entity.external_workspace_id):
        entity.config.contacts = SurfaceContactPolicy(
            answer=ContactAnswer.ANYONE, looked_after_by=owner_user_id
        )


async def workspace_belongs_to_pod(
    uow, surface: AgentSurfaceEntity, team_id: str | None
) -> bool:
    """Whether Lemma's own app manages this workspace for the pod's organization."""
    if not team_id:
        return False
    from app.modules.agent_surfaces.infrastructure.repositories.slack_manager_repository import (
        SlackManagerRepository,
    )
    from app.modules.pod.contracts.agent_access import pod_organization_id

    install = await SlackManagerRepository(uow.session).install_for_team(team_id)
    if install is None:
        return False
    return await pod_organization_id(uow, surface.pod_id) == install.organization_id


#: Whether a surface's pod's organization holds this Slack workspace.
WorkspaceHeld = Callable[[AgentSurfaceEntity, str | None], Awaitable[bool]]


def workspace_held_by(uow) -> WorkspaceHeld:
    """``workspace_belongs_to_pod`` bound to a unit of work, for a door to ask."""

    async def held(surface: AgentSurfaceEntity, team_id: str | None) -> bool:
        return await workspace_belongs_to_pod(uow, surface, team_id)

    return held

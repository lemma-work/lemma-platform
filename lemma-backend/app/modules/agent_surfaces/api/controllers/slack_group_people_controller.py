"""Who is in a Slack group, read from Slack, for the group's page.

Its own module because ``space_group_controller`` is at the size the
architecture ratchet allows; it reads the group the same way that one does.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep, require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.api.controllers.space_group_controller import (
    _readable_group,
    _surface,
)
from app.modules.agent_surfaces.api.group_access import GroupAccess
from app.modules.agent_surfaces.services.slack_channel_people import channel_people

router = APIRouter(prefix="/pods/{pod_id}/groups", tags=["Agent Surfaces"])


class SlackChannelPersonResponse(BaseModel):
    slack_user_id: str
    name: str
    #: ``in`` (one of the pod's), ``workspace`` (answered as the public is), or
    #: ``outside`` (a guest, or from another company).
    standing: str
    user_id: UUID | None = None


class SlackChannelPeopleResponse(BaseModel):
    items: list[SlackChannelPersonResponse]
    #: How many more are in the channel than were looked at.
    more: int = 0


@router.get(
    "/{group_id}/slack-people",
    operation_id="agent.group.slack_people",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def slack_group_people(
    pod_id: UUID, group_id: UUID, user: CurrentUser, ctx: PodContextDep, uow: UoWDep
) -> SlackChannelPeopleResponse:
    """Who is in this Slack channel, and which of them are the pod's own."""
    detail = await _readable_group(
        pod_id=pod_id, group_id=group_id, ctx=ctx, uow=uow, viewer_id=user.id
    )
    group = detail.group
    if group.platform != "SLACK" or not group.external_channel_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not a Slack channel")
    # Who is in a channel -- guests and other companies' people included -- is
    # for whoever runs the group, not for anyone who may read it.
    if not await GroupAccess(
        ctx=ctx, uow=uow, pod_id=pod_id, viewer_id=user.id
    ).manages(detail):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only whoever runs this group sees its people",
        )
    surface = await _surface(uow, group.surface_id)
    read = await channel_people(
        uow, surface=surface, channel_id=group.external_channel_id
    )
    return SlackChannelPeopleResponse(
        items=[
            SlackChannelPersonResponse(
                slack_user_id=person.slack_user_id,
                name=person.name,
                standing=person.standing.value,
                user_id=person.user_id,
            )
            for person in read.people
        ],
        more=read.more,
    )

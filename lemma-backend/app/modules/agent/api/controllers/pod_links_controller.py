"""Which other pods may ask this one, with nobody present: its links.

The routes live under the pod being asked, because its people make the call:
connecting needs ``pod.member.manage`` here (letting another pod in is the same
decision as letting a person in), and the person connecting must also be in
the pod that will ask (``services/pod_link_service``). A link is grants to
``POD:<id>`` (``infrastructure/pod_link_queries``), so connecting again replaces
what it shares and disconnecting removes all of it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import (
    pod_from_path,
    require_action,
    require_pod_membership,
)
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.modules.agent.services.pod_link_service import PodLinkService, PodLinkView

router = APIRouter(prefix="/pods/{pod_id}/pod-links", tags=["agent_asks"])

LINK_MEMBERSHIP = require_pod_membership("see which pods can ask this one")
LINK_ADMIN = require_action(Permissions.POD_MEMBER_MANAGE, pod_from_path)


class SharedResourceBody(BaseModel):
    resource_type: ResourceType
    resource_name: str = Field(description="The table's name, or the folder's path.")
    permission_ids: list[str] = Field(
        description="Reading only: datastore.table.read and datastore.record.read "
        "for a table, folder.read for a folder."
    )


class ConnectPodRequest(BaseModel):
    shares: list[SharedResourceBody] = Field(
        default_factory=list,
        description="What the asking pod may read here, past what is Public.",
    )


class PodLinkResponse(BaseModel):
    pod_id: UUID = Field(description="The other pod.")
    name: str
    description: str | None = None
    icon_url: str | None = None
    steward_user_id: UUID | None = Field(
        default=None, description="Who connected it, and looks after it."
    )
    steward_name: str | None = None
    shared: list[SharedResourceBody]


class PodLinkListResponse(BaseModel):
    items: list[PodLinkResponse]


def _response(view: PodLinkView) -> PodLinkResponse:
    return PodLinkResponse(
        pod_id=view.pod.id,
        name=view.pod.name,
        description=view.pod.description,
        icon_url=view.pod.icon_url,
        steward_user_id=view.steward_user_id,
        steward_name=view.steward_name,
        shared=[
            SharedResourceBody(
                resource_type=one.resource_type,
                resource_name=one.name,
                permission_ids=one.permission_ids,
            )
            for one in view.shared
        ],
    )


@router.get(
    "",
    response_model=PodLinkListResponse,
    operation_id="agent.pod_link.list",
    dependencies=[LINK_MEMBERSHIP],
    summary="List Pods That Can Ask This Pod",
    description=(
        "The other pods connected to this one: each may ask this pod's "
        "assistant with nobody present, and read what is Public here plus what "
        "this pod shared with it."
    ),
)
async def list_pod_links(pod_id: UUID, uow: UoWDep) -> PodLinkListResponse:
    views = await PodLinkService(uow).incoming(answering_pod_id=pod_id)
    return PodLinkListResponse(items=[_response(view) for view in views])


@router.put(
    "/{asking_pod_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.pod_link.connect",
    dependencies=[LINK_ADMIN],
    summary="Connect A Pod To This One",
    description=(
        "Let another pod in this organization ask this one, sharing the tables "
        "and folders named for reading. Connecting again replaces what is "
        "shared. Needs pod.member.manage here, and membership of the other pod; "
        "the caller looks after the link."
    ),
    responses={
        400: {"description": "Something named can't be shared, or not for that"},
        409: {"description": "The two pods can't be connected"},
    },
)
async def connect_pod(
    pod_id: UUID,
    asking_pod_id: UUID,
    data: ConnectPodRequest,
    user: CurrentUser,
    uow: UoWDep,
) -> Response:
    await PodLinkService(uow).connect(
        answering_pod_id=pod_id,
        asking_pod_id=asking_pod_id,
        user_id=user.id,
        shares=data.shares,
    )
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{asking_pod_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.pod_link.disconnect",
    dependencies=[LINK_ADMIN],
    summary="Disconnect A Pod From This One",
    description="Stop another pod asking this one, and take back what it was shared.",
    responses={404: {"description": "That pod is not connected to this one"}},
)
async def disconnect_pod(pod_id: UUID, asking_pod_id: UUID, uow: UoWDep) -> Response:
    if not await PodLinkService(uow).disconnect(
        answering_pod_id=pod_id, asking_pod_id=asking_pod_id
    ):
        raise DomainError(
            "That pod is not connected to this one.",
            code="pod_link_not_found",
            status_code=404,
        )
    await uow.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

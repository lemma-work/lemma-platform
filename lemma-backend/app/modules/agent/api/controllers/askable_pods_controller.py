"""The other pods this pod's assistant can ask, for the person looking.

Asking another pod runs as the person who asks, so the answer to "who can it
ask" is per person: the other pods in this organization that they belong to.
That is the same list the assistant's own `list_teammates` tool reads
(``infrastructure/pod_ask_queries.askable_pods``), which is the point of serving
it: what the app says the teammate can ask is exactly what it will be allowed to.

Each says how it is reached: ``through_you`` when the caller is in it too, so
the assistant can ask as them; ``connected`` when that pod linked itself to this
one, so it can be asked with nobody present. Often both.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_pod_membership
from app.modules.agent.infrastructure.pod_ask_queries import askable_pods
from app.modules.pod.contracts.agent_access import pod_organization_id

router = APIRouter(prefix="/pods/{pod_id}/askable-pods", tags=["agent_asks"])

ASKABLE_PODS_MEMBERSHIP = require_pod_membership("see which pods this one can ask")


class AskablePodResponse(BaseModel):
    pod_id: UUID
    name: str
    description: str | None = Field(default=None, description="What the pod is for.")
    icon_url: str | None = None
    through_you: bool = Field(
        description="The caller is in it too, so it can be asked as them."
    )
    connected: bool = Field(
        description="It linked itself to this pod, so it can be asked with nobody present."
    )


class AskablePodListResponse(BaseModel):
    items: list[AskablePodResponse]


@router.get(
    "",
    response_model=AskablePodListResponse,
    operation_id="agent.askable_pod.list",
    dependencies=[ASKABLE_PODS_MEMBERSHIP],
    summary="List Pods This Pod Can Ask",
    description=(
        "The other pods this pod's assistant can ask: those the caller is also "
        "a member of, which it asks as the caller, and those connected to this "
        "pod, which it can ask with nobody present."
    ),
)
async def list_askable_pods(
    pod_id: UUID, user: CurrentUser, uow: UoWDep
) -> AskablePodListResponse:
    pods = await askable_pods(
        uow,
        user_id=user.id,
        organization_id=await pod_organization_id(uow, pod_id),
        pod_id=pod_id,
    )
    return AskablePodListResponse(
        items=[
            AskablePodResponse(
                pod_id=pod.pod_id,
                name=pod.name,
                description=pod.description,
                icon_url=pod.icon_url,
                through_you=pod.through_you,
                connected=pod.connected,
            )
            for pod in pods
        ]
    )

"""The hook points standing work in a pod can attach to."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, JsonValue

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_pod_membership
from app.modules.schedule.domain.event_catalog import BUILT_IN_EVENTS
from app.modules.schedule.domain.schedule import MCP_EVENT_SOURCE, ScheduleType

router = APIRouter(prefix="/pods/{pod_id}/events", tags=["Schedules"])


class EventDescriptorResponse(BaseModel):
    """An MCP Events descriptor, plus the schedule type that serves it."""

    name: str
    title: str
    description: str
    schedule_type: ScheduleType
    input_schema: dict[str, JsonValue]
    payload_schema: dict[str, JsonValue]
    #: For a connected MCP server's event: the account a schedule on it
    #: listens through, the server it is on, and the event's own name -- what
    #: a WEBHOOK schedule's config names as `event`.
    account_id: UUID | None = None
    #: Which of the caller's accounts that is, in words -- two accounts on the
    #: same server offer the same events, and the account is the difference.
    account_label: str | None = None
    server: str | None = None
    event: str | None = None


class EventCatalogResponse(BaseModel):
    items: list[EventDescriptorResponse]


@router.get(
    "",
    response_model=EventCatalogResponse,
    operation_id="schedule.event.list",
    dependencies=[require_pod_membership("list this pod's events", enumerates=True)],
)
async def list_events(
    pod_id: UUID, user: CurrentUser, uow: UoWDep
) -> EventCatalogResponse:
    """What standing work here can start on: the platform's own events, then
    every event on an MCP server the caller connected in this organization.
    A connected app's catalog triggers are listed per install
    (`connector.trigger.list`)."""
    from app.modules.connectors.contracts.mcp_events import mcp_event_offers
    from app.modules.pod.contracts.members import pod_organization_id

    items = [
        EventDescriptorResponse(
            name=event.name,
            title=event.title,
            description=event.description,
            schedule_type=event.schedule_type,
            input_schema=event.input_schema,
            payload_schema=event.payload_schema,
        )
        for event in BUILT_IN_EVENTS
    ]
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        return EventCatalogResponse(items=items)
    for offer in await mcp_event_offers(
        uow, user_id=user.id, organization_id=organization_id
    ):
        items.append(
            EventDescriptorResponse(
                name=f"{MCP_EVENT_SOURCE}.{offer.server}.{offer.name}",
                title=f"{offer.server}: {offer.name}",
                description=offer.description or offer.name,
                schedule_type=ScheduleType.WEBHOOK,
                input_schema=offer.input_schema,
                payload_schema=offer.payload_schema,
                account_id=offer.account_id,
                account_label=offer.account_label,
                server=offer.server,
                event=offer.name,
            )
        )
    return EventCatalogResponse(items=items)

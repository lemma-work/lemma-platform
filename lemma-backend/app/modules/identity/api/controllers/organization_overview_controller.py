"""An organization's overview, for one member: one read per question, not per pod.

What waits on the member, what moved in each pod, every app, and when they last
looked at Home -- each across every pod they may open in the organization. An
overview of all of an organization's pods used to cost about ten requests per
pod and still undercounted; these answer each question in one request whose
queries do not grow with the pod count (``load_needs_you`` names the one narrow
exception).

Split from ``organization_navigation_controller`` for size, and because these
read inside pods where that one reads only the pods' own columns. A caller who
is not a member of the organization is refused with 403, as ``org.home`` is,
whether or not the organization exists.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import AfterValidator

from app.core.api.dependencies import CurrentUser, UoWDep
from app.modules.identity.api.schemas.organization_overview_schemas import (
    AutomationResponse,
    DayCountResponse,
    HomeSeenRequest,
    HomeSeenResponse,
    NeedsYouItemResponse,
    NeedsYouResponse,
    OrganizationAppListResponse,
    OrganizationAppResponse,
    OrganizationRecentsResponse,
    PodRecentsResponse,
    RecentChatResponse,
)
from app.modules.identity.domain.time_zones import DEFAULT_ZONE, known_zone
from app.modules.identity.infrastructure.organization_home_seen import (
    HomeSeen,
    mark_home_seen,
    read_home_seen,
)
from app.modules.identity.services.organization_apps import load_organization_apps
from app.modules.identity.services.organization_needs_you import load_needs_you
from app.modules.identity.services.organization_recents import load_recents

router = APIRouter(
    prefix="/organizations",
    tags=["Organizations"],
    redirect_slashes=False,
)

#: What one Needs-you read lists at most. A person with more waiting than this
#: is shown the oldest, with ``truncated`` set.
MAX_NEEDS_YOU = 200
#: The longest window the recents count over.
MAX_DAYS = 31
#: The most chats one pod lists in recents.
MAX_CHATS_PER_POD = 25


def _not_a_member() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
    )


def _zone_name(value: str) -> str:
    """An IANA zone name this host knows, else a 422 naming the field."""
    return str(known_zone(value))


#: A query parameter's validator runs only when ``Query`` sits inside the
#: ``Annotated`` with it; given as the default instead, FastAPI drops it.
ZoneName = Annotated[
    str,
    Query(
        alias="timezone",
        description="IANA time zone the days are counted in, e.g. Europe/Berlin.",
    ),
    AfterValidator(_zone_name),
]


@router.get(
    "/{organization_id}/needs-you",
    status_code=status.HTTP_200_OK,
    operation_id="org.needs_you.list",
    summary="List What Waits On You Across The Organization",
    description=(
        "Everything waiting on the current user in every pod they may open in "
        "the organization, as one list, oldest first: questions, approvals and "
        "Agent Host permission requests in their own conversations (one item "
        "per conversation); workflow forms and decisions assigned to them; and "
        "contacts waiting on a person, in pods where they hold `channel.read` "
        "and `contact.read`. Each item names its pod and what to open: a "
        "conversation, a workflow run or a channel."
    ),
    response_model=NeedsYouResponse,
)
async def list_needs_you(
    organization_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    limit: int = Query(default=100, ge=1, le=MAX_NEEDS_YOU),
) -> NeedsYouResponse:
    needs = await load_needs_you(
        uow, user_id=user.id, organization_id=organization_id, limit=limit
    )
    if needs is None:
        raise _not_a_member()
    return NeedsYouResponse(
        items=[NeedsYouItemResponse.model_validate(item) for item in needs.items],
        limit=limit,
        truncated=needs.truncated,
    )


@router.get(
    "/{organization_id}/recents",
    status_code=status.HTTP_200_OK,
    operation_id="org.recents.get",
    summary="Get What Moved Lately In Each Pod",
    description=(
        "For every pod the current user may open in the organization, quiet "
        "ones included: their latest chats (conversations of no channel that "
        "they talk in, or that wait on them), the workflows and schedules that "
        "ran in the window -- one each, with the newest run and a count -- and "
        "a count per day of their conversations by the day each was last "
        "active, split into started by an agent (a schedule, a workflow, a "
        "notification, a task or another pod's ask) and by a person. Days are "
        "calendar days in `timezone`, the window ending today."
    ),
    response_model=OrganizationRecentsResponse,
)
async def get_recents(
    organization_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    days: int = Query(default=14, ge=1, le=MAX_DAYS),
    chats: int = Query(
        default=10, ge=1, le=MAX_CHATS_PER_POD, description="Chats listed per pod."
    ),
    zone: ZoneName = DEFAULT_ZONE,
) -> OrganizationRecentsResponse:
    recents = await load_recents(
        uow.session,
        user_id=user.id,
        organization_id=organization_id,
        days=days,
        chats_per_pod=chats,
        zone_name=zone,
    )
    if recents is None:
        raise _not_a_member()
    return OrganizationRecentsResponse(
        organization_id=organization_id,
        timezone=zone,
        days=days,
        since=recents.since,
        pods=[
            PodRecentsResponse(
                pod_id=pod.pod_id,
                pod_name=pod.pod_name,
                chats=[RecentChatResponse.model_validate(chat) for chat in pod.chats],
                automations=[
                    AutomationResponse.model_validate(each) for each in pod.automations
                ],
                activity=[DayCountResponse.model_validate(day) for day in pod.activity],
            )
            for pod in recents.pods
        ],
    )


@router.get(
    "/{organization_id}/apps",
    status_code=status.HTTP_200_OK,
    operation_id="org.apps.list",
    summary="List Apps Across The Organization",
    description=(
        "Every app the current user may open in every pod they may open in the "
        "organization, most recently updated first. Each app is filtered by its "
        "own visibility against the user's permissions in its pod."
    ),
    response_model=OrganizationAppListResponse,
)
async def list_organization_apps(
    organization_id: UUID, user: CurrentUser, uow: UoWDep
) -> OrganizationAppListResponse:
    listed = await load_organization_apps(
        uow.session, user_id=user.id, organization_id=organization_id
    )
    if listed is None:
        raise _not_a_member()
    return OrganizationAppListResponse(
        items=[
            OrganizationAppResponse(
                id=each.app.id,
                pod_id=each.pod_id,
                pod_name=each.pod_name,
                name=each.app.name,
                description=each.app.description,
                url=each.app.url,
                status=each.app.status,
                visibility=each.app.visibility,
                updated_at=each.app.updated_at,
            )
            for each in listed
        ]
    )


def _seen(seen: HomeSeen | None) -> HomeSeenResponse:
    if seen is None:
        raise _not_a_member()
    return HomeSeenResponse(
        seen_at=seen.seen_at, previous_seen_at=seen.previous_seen_at
    )


@router.get(
    "/{organization_id}/home/seen",
    status_code=status.HTTP_200_OK,
    operation_id="org.home.seen.get",
    summary="Get When You Last Looked At Home",
    description=(
        "When the current user last marked the organization's Home seen, so it "
        "can say what moved since. Per user and per organization."
    ),
    response_model=HomeSeenResponse,
)
async def get_home_seen(
    organization_id: UUID, user: CurrentUser, uow: UoWDep
) -> HomeSeenResponse:
    return _seen(
        await read_home_seen(
            uow.session, user_id=user.id, organization_id=organization_id
        )
    )


@router.put(
    "/{organization_id}/home/seen",
    status_code=status.HTTP_200_OK,
    operation_id="org.home.seen.set",
    summary="Mark Home Seen",
    description=(
        "Mark the organization's Home seen by the current user, as of "
        "`seen_at` (now when omitted; a future moment is read as now). The "
        "mark only moves forward, so an older tab cannot undo a newer one. "
        "Returns the mark and what it was before."
    ),
    response_model=HomeSeenResponse,
)
async def set_home_seen(
    organization_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    body: HomeSeenRequest | None = Body(default=None),
) -> HomeSeenResponse:
    now = datetime.now(timezone.utc)
    asked = body.seen_at if body is not None else None
    return _seen(
        await mark_home_seen(
            uow.session,
            user_id=user.id,
            organization_id=organization_id,
            at=min(asked, now) if asked is not None else now,
        )
    )

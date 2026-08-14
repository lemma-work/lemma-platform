"""Read-only endpoints that render organization navigation in one call each.

Split from ``organization_controller`` because they are a different kind of
endpoint: no mutation, no service dependency, and a deliberate query budget.
See ``services/organization_navigation`` for why there are two shapes rather
than one.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from app.core.api.dependencies import UoWDep
from app.modules.identity.api.schemas.organization_schemas import (
    HomeAgentResponse,
    HomeAppResponse,
    HomePodResponse,
    NavigationOrganizationResponse,
    NavigationPodResponse,
    NavigationResponse,
    OrganizationHomeResponse,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.organization_home_cache import (
    get_cached_organization_home,
    set_cached_organization_home,
)
from app.composition.organization_navigation import (
    load_navigation,
    load_organization_home,
)

router = APIRouter(
    prefix="/organizations",
    tags=["Organizations"],
    redirect_slashes=False,
)


@router.get(
    "/navigation",
    status_code=status.HTTP_200_OK,
    operation_id="org.navigation",
    summary="List Organizations And Their Pods",
    description=(
        "Every organization the current user belongs to, each with the pods they "
        "can see in it. Replaces fetching the organization list and then one pod "
        "list per organization; the payload stays shallow so it does not grow "
        "with the contents of each pod."
    ),
    response_model=NavigationResponse,
)
async def get_navigation(request: Request, uow: UoWDep) -> NavigationResponse:
    """Sidebar navigation for every organization, in two queries."""
    user: UserEntity = request.state.user
    organizations = await load_navigation(session=uow.session, user_id=user.id)
    return NavigationResponse(
        items=[
            NavigationOrganizationResponse(
                id=organization.id,
                name=organization.name,
                slug=organization.slug,
                role=organization.role,
                pods=[
                    NavigationPodResponse(
                        id=pod.id,
                        name=pod.name,
                        description=pod.description,
                        icon_url=pod.icon_url,
                        updated_at=pod.updated_at,
                    )
                    for pod in organization.pods
                ],
            )
            for organization in organizations
        ]
    )


@router.get(
    "/{org_id}/home",
    status_code=status.HTTP_200_OK,
    operation_id="org.home",
    summary="Get Organization Home",
    description=(
        "One organization's landing page: every pod the current user can see, "
        "with its apps, its agents, and the user's roles in that pod. Replaces "
        "fetching apps and agents per pod. Cached briefly per user."
    ),
    response_model=OrganizationHomeResponse,
)
async def get_organization_home(
    request: Request, org_id: UUID, uow: UoWDep
) -> OrganizationHomeResponse:
    """One organization in detail, cached per (organization, user).

    Cached because this is a landing page: it is re-fetched on every visit and
    on every tab focus, while the content behind it changes far more slowly.
    Roles ride along in the cache, so a role change can take up to the TTL to
    show here — accepted deliberately, because this is a screen you have not
    refreshed rather than an authorization decision. Every permission check
    inside a pod resolves roles live.

    Keyed by user as well as organization: two members of one organization see
    different pods, so an organization-only key would serve one person's
    listing to another.
    """
    user: UserEntity = request.state.user
    cached = await get_cached_organization_home(
        organization_id=org_id, user_id=user.id
    )
    if cached is not None:
        return OrganizationHomeResponse.model_validate(cached)

    home = await load_organization_home(
        session=uow.session, user_id=user.id, organization_id=org_id
    )
    if home is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied"
        )

    response = OrganizationHomeResponse(
        organization_id=home.organization_id,
        name=home.name,
        slug=home.slug,
        role=home.role,
        pods=[
            HomePodResponse(
                id=pod.id,
                name=pod.name,
                description=pod.description,
                icon_url=pod.icon_url,
                roles=pod.roles,
                apps=[
                    HomeAppResponse(
                        id=app.id,
                        name=app.name,
                        description=app.description,
                        url=app.url,
                        status=app.status,
                    )
                    for app in pod.apps
                ],
                agents=[
                    HomeAgentResponse(
                        id=agent.id,
                        name=agent.name,
                        description=agent.description,
                        icon_url=agent.icon_url,
                    )
                    for agent in pod.agents
                ],
            )
            for pod in home.pods
        ],
    )
    await set_cached_organization_home(
        organization_id=org_id,
        user_id=user.id,
        payload=response.model_dump(mode="json"),
    )
    return response

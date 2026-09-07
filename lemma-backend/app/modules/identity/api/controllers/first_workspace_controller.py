"""One call that leaves the caller with somewhere to work.

The web onboarding used to do this itself, in TypeScript: look for an
organization, look for one matching the email domain, otherwise invent a name
and create one. Chat surfaces onboard people who never load the app, so the
same decision had to exist in the backend -- and two implementations of "which
organization does this person belong to" drift from each other inside a
release.

So the frontend calls this instead. It is deliberately not `POST /organizations`
with the frontend choosing a name: the naming, the domain-join preference and
the first pod are the parts that must not be decided twice.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.modules.identity.api.dependencies import get_organization_service
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.first_workspace import ensure_first_workspace

router = APIRouter(
    prefix="/users/me",
    tags=["Users"],
    redirect_slashes=False,
)


class FirstWorkspaceRequest(BaseModel):
    with_pod: bool = Field(
        default=True,
        description=(
            "Whether a newly created organization also gets a first pod. False "
            "for callers about to create a pod of their own -- the bundle "
            "importer lands somebody in the pod it is importing into, and a "
            "spare empty one beside it is clutter rather than a welcome."
        ),
    )


class FirstWorkspaceResponse(BaseModel):
    organization_id: UUID
    pod_id: UUID | None = None
    entry: str = Field(
        description=(
            "Which door they came through: `existing` when they already "
            "belonged somewhere, `domain_join` when a colleague had already "
            "claimed their email domain, `new_org` when one was created."
        )
    )


@router.post(
    "/first-workspace",
    status_code=status.HTTP_200_OK,
    # Out of the published spec, like the other onboarding mechanics under
    # `/auth/mobile-verification/*`. This is how *this* web app gets its first
    # workspace, not something an SDK caller should be reaching for -- an API
    # client that wants an organization has `POST /organizations` and can name
    # it. Keeping it out also keeps a private detail out of both SDKs.
    include_in_schema=False,
    operation_id="users.ensure_first_workspace",
    summary="Ensure The Current User Has A Workspace",
    description=(
        "Returns the organization this person belongs to, creating one only if "
        "they belong to none. Joins an organization that already claimed their "
        "email domain in preference to making a second one, and creates a first "
        "pod alongside a newly created organization. Safe to call repeatedly: "
        "somebody who already belongs somewhere gets that organization back and "
        "nothing is created."
    ),
    response_model=FirstWorkspaceResponse,
)
async def ensure_workspace(
    request: Request, uow: UoWDep, data: FirstWorkspaceRequest | None = None
) -> FirstWorkspaceResponse:
    # `request.state.user` is the principal the token carries -- an id and
    # nothing else, deliberately, so that most requests need no user read. The
    # naming needs an address and a name, so this is one of the few that does.
    principal = request.state.user
    user = await UserRepository(uow).get(principal.id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    workspace = await ensure_first_workspace(
        uow,
        organization_service=get_organization_service(uow),
        user_id=user.id,
        email=str(user.email),
        full_name=" ".join(
            part for part in (user.first_name, user.last_name) if part
        ).strip()
        or None,
        with_pod=(data or FirstWorkspaceRequest()).with_pod,
    )
    return FirstWorkspaceResponse(
        organization_id=workspace.organization_id,
        pod_id=workspace.pod_id,
        entry=workspace.entry,
    )

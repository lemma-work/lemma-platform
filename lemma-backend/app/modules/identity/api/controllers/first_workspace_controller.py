"""The web's way into the one first-workspace rule every sign-up follows."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.api.dependencies import UoWDep
from app.modules.identity.api.dependencies import get_organization_service
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.first_workspace import (
    WorkspaceEntry,
    ensure_first_workspace,
)

router = APIRouter(
    prefix="/users/me",
    tags=["Users"],
    redirect_slashes=False,
)


class FirstWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    with_pod: bool = Field(
        default=True,
        description=(
            "Make a private pod and assistant when the person would otherwise "
            "land in no Lemma: only in an older organization that has no Lemma "
            "of its own. A new organization is always made with one."
        ),
    )
    own_organization: bool = Field(
        default=False,
        description=(
            "Skip their company's organization, even when it is open to their "
            "work domain, and make a workspace of their own. It is made by "
            "invitation only and named after the person. Invitations and an "
            "organization they already belong to still come first."
        ),
    )
    team: bool | None = Field(
        default=None,
        description=(
            "What kind of organization to make when one is made. Omitted: a "
            "company's, named after it and open to colleagues from the same "
            "domain, when the person's address is verified and on a work "
            "domain; otherwise a workspace of one. false: a workspace of one "
            "whatever the address, for a deployment nobody else can reach."
        ),
    )


class FirstWorkspaceResponse(BaseModel):
    organization_id: UUID
    organization_name: str
    pod_id: UUID | None = Field(
        default=None,
        description=(
            "Where the person lands: the pod an invitation named, else the "
            "organization's own Lemma, else the private pod `with_pod` asked "
            "for. Null when there is none."
        ),
    )
    pod_name: str | None = None
    assistant_id: UUID | None = None
    entry: WorkspaceEntry
    organization_created: bool
    pod_created: bool = Field(
        description="Whether the pod in `pod_id` was made by this call."
    )
    organization_open_to_domain: str | None = Field(
        default=None,
        description=(
            "The email domain whose verified addresses may join the "
            "organization on their own, when it takes colleagues that way. "
            "Its owner can close it in Settings."
        ),
    )
    organization_pod_id: UUID | None = Field(
        default=None,
        description=(
            "The organization's own Lemma, the one everyone in it shares. "
            "Differs from `pod_id` when an invitation brought the person to "
            "another pod, and is null in an organization that has none."
        ),
    )


@router.post(
    "/first-workspace",
    status_code=status.HTTP_200_OK,
    operation_id="users.ensure_first_workspace",
    summary="Ensure The Current User Has A Workspace",
    description=(
        "Put the current user in an organization, making one if they belong to "
        "none, and say which Lemma they land in. Pending invitations are "
        "accepted first, then an organization they already belong to is used, "
        "then their company's organization open to their verified work domain "
        "is joined; otherwise one is made, with a Lemma everyone in it shares. "
        "Safe to repeat: a second call makes nothing new."
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

    asked = data or FirstWorkspaceRequest()
    workspace = await ensure_first_workspace(
        uow,
        organization_service=get_organization_service(uow),
        user_id=user.id,
        email=str(user.email),
        full_name=" ".join(
            part for part in (user.first_name, user.last_name) if part
        ).strip()
        or None,
        with_pod=asked.with_pod,
        own_organization=asked.own_organization,
        team=asked.team,
    )
    return FirstWorkspaceResponse(
        organization_id=workspace.organization_id,
        organization_name=workspace.organization_name,
        pod_id=workspace.pod_id,
        pod_name=workspace.pod_name,
        entry=workspace.entry,
        assistant_id=workspace.assistant_id,
        organization_created=workspace.organization_created,
        pod_created=workspace.pod_created,
        organization_open_to_domain=workspace.organization_open_to_domain,
        organization_pod_id=workspace.organization_pod_id,
    )

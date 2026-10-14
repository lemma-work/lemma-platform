"""The web side of Lemma's own Slack app managing a workspace.

Two things happen in a browser rather than in Slack: authorising Lemma's app,
which a reach sheet turns into "this workspace is ours" (``PUT .../manager``),
and proving which Lemma person a Slack user is, from the link their own App
Home showed them (``POST /surface-setup/slack/people``).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.services.slack_manager import show_home
from app.modules.agent_surfaces.services.slack_manager_context import load_context
from app.modules.agent_surfaces.services.slack_manager_install import (
    ManagedWorkspace,
    bind_manager,
    link_person,
    managed_workspaces,
    preview_person_link,
)
from app.modules.pod.contracts.agent_access import pod_organization_id

router = APIRouter(prefix="/pods/{pod_id}/surface-setup/slack", tags=["Agent Surfaces"])
people_router = APIRouter(prefix="/surface-setup/slack", tags=["Agent Surfaces"])


class SlackManagedWorkspaceResponse(BaseModel):
    """A Slack workspace Lemma's own app manages for this organization."""

    team_id: str
    team_name: str | None = None


class SlackManagedWorkspaceListResponse(BaseModel):
    items: list[SlackManagedWorkspaceResponse]


class SlackManagerBindRequest(BaseModel):
    account_id: UUID = Field(
        description="The account made by authorising Lemma's own Slack app."
    )


class SlackPersonLinkRequest(BaseModel):
    """The values in the link a Slack user's App Home showed them."""

    team_id: str = Field(min_length=1, max_length=32)
    slack_user_id: str = Field(min_length=1, max_length=32)
    expires: int
    signature: str = Field(min_length=1, max_length=512)


def _response(item: ManagedWorkspace) -> SlackManagedWorkspaceResponse:
    return SlackManagedWorkspaceResponse(team_id=item.team_id, team_name=item.team_name)


async def _organization(uow, pod_id: UUID) -> UUID:
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        raise AgentSurfaceValidationError(f"Pod {pod_id} not found")
    return organization_id


@router.get(
    "/manager",
    operation_id="agent.surface.slack_manager.list",
    dependencies=[require_action(Permissions.AGENT_READ)],
)
async def list_slack_managed_workspaces(
    pod_id: UUID, uow: UoWDep
) -> SlackManagedWorkspaceListResponse:
    """The Slack workspaces Lemma's own app manages for this pod's organization."""
    items = await managed_workspaces(uow, await _organization(uow, pod_id))
    return SlackManagedWorkspaceListResponse(items=[_response(item) for item in items])


@router.put(
    "/manager",
    operation_id="agent.surface.slack_manager.bind",
    dependencies=[require_action(Permissions.AGENT_UPDATE)],
)
async def bind_slack_manager(
    pod_id: UUID, request: SlackManagerBindRequest, user: CurrentUser, uow: UoWDep
) -> SlackManagedWorkspaceResponse:
    """Make Lemma's own app this workspace's manager. The workspace becomes the
    organization's, and the person who authorised it is known in Slack."""
    bound = await bind_manager(
        uow,
        user_id=user.id,
        organization_id=await _organization(uow, pod_id),
        account_id=request.account_id,
    )
    return _response(bound)


class SlackPersonLinkPreviewResponse(BaseModel):
    """Whose Slack account a link would connect, before it is connected."""

    team_id: str
    team_name: str | None = None
    slack_user_name: str | None = None


@people_router.post(
    "/people/preview", operation_id="agent.surface.slack_person.preview"
)
async def preview_slack_person_link(
    request: SlackPersonLinkRequest, user: CurrentUser, uow: UoWDep
) -> SlackPersonLinkPreviewResponse:
    """Say whose Slack account this link would connect to the signed-in person.

    Connects nothing. The page shows it and asks first, so a link somebody
    else sent cannot connect their Slack account to yours unannounced.
    """
    del user
    preview = await preview_person_link(
        uow,
        team_id=request.team_id,
        slack_user_id=request.slack_user_id,
        expires=request.expires,
        signature=request.signature,
    )
    return SlackPersonLinkPreviewResponse(
        team_id=preview.team_id,
        team_name=preview.team_name,
        slack_user_name=preview.slack_user_name,
    )


@people_router.post("/people", operation_id="agent.surface.slack_person.link")
async def link_slack_person(
    request: SlackPersonLinkRequest, user: CurrentUser, uow: UoWDep
) -> SlackManagedWorkspaceResponse:
    """Say that the signed-in person is the Slack user whose App Home showed the link."""
    linked = await link_person(
        uow,
        user_id=user.id,
        team_id=request.team_id,
        slack_user_id=request.slack_user_id,
        expires=request.expires,
        signature=request.signature,
    )
    # Their Home was the "connect" view; show them their teammates instead.
    ctx = await load_context(
        uow, team_id=request.team_id, slack_user_id=request.slack_user_id
    )
    if ctx is not None:
        await show_home(uow, ctx)
    return _response(linked)

"""The web side of Lemma's own Slack app managing a workspace.

Three things happen in a browser rather than in Slack: authorising Lemma's
app, which a reach sheet turns into "this workspace is ours" (``PUT
.../manager``); "Add to Slack" from outside the app, which starts that grant
for an organization and lets it adopt the workspace on its own (``GET``/``POST
/surface-setup/slack/install``); and proving which Lemma person a Slack user
is, by Sign in with Slack started from the link their own App Home shows
(``POST /surface-setup/slack/people/sign-in``, ending at its callback).
"""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.api.dependencies import BrowserUserId, CurrentUser, UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.config import settings
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceValidationError,
    SlackSignInError,
)
from app.modules.agent_surfaces.services.slack_install import (
    install_available,
    start_slack_install,
)
from app.modules.agent_surfaces.services.slack_manager import (
    refresh_home_of,
)
from app.modules.agent_surfaces.services.slack_manager_install import (
    ManagedWorkspace,
    bind_manager,
    managed_workspaces,
)
from app.modules.agent_surfaces.services.slack_person_proof import (
    CALLBACK_PATH,
    finish_sign_in,
    start_sign_in,
)
from app.modules.pod.contracts.agent_access import pod_organization_id

router = APIRouter(prefix="/pods/{pod_id}/surface-setup/slack", tags=["Agent Surfaces"])
people_router = APIRouter(prefix="/surface-setup/slack", tags=["Agent Surfaces"])

#: The web page a sign-in starts from and comes back to.
_CONNECT_PAGE = "/slack/connect"


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


class SlackInstallAvailabilityResponse(BaseModel):
    """Whether "Add to Slack" can work on this server, asked before anything
    is made for it."""

    available: bool


class SlackInstallRequest(BaseModel):
    """Which organization Lemma's app joins the workspace for."""

    organization_id: UUID


class SlackInstallResponse(BaseModel):
    """Slack's consent page for Lemma's own app, to send the browser to."""

    authorization_url: str


class SlackSignInRequest(BaseModel):
    """Whom the App Home link was for."""

    team_id: str = Field(min_length=1, max_length=32)
    slack_user_id: str = Field(min_length=1, max_length=32)


class SlackSignInResponse(BaseModel):
    """Where to send the browser: Slack's own sign-in page."""

    authorization_url: str


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
    organization's, and the person who authorised it is known in Slack.

    Only for a grant this browser finished signed in as the caller: a consent
    link somebody else completed is refused."""
    bound = await bind_manager(
        uow,
        user_id=user.id,
        organization_id=await _organization(uow, pod_id),
        account_id=request.account_id,
    )
    return _response(bound)


@people_router.get("/install", operation_id="agent.surface.slack_install.availability")
async def slack_install_availability(
    user: CurrentUser, uow: UoWDep
) -> SlackInstallAvailabilityResponse:
    """Whether Lemma's own Slack app is set up on this server, so "Add to
    Slack" can work -- checked before a person with no organization is given
    one for it."""
    return SlackInstallAvailabilityResponse(available=await install_available(uow))


@people_router.post("/install", operation_id="agent.surface.slack_install.start")
async def start_slack_install_flow(
    request: SlackInstallRequest, user: CurrentUser, uow: UoWDep
) -> SlackInstallResponse:
    """Add Lemma to a Slack workspace from "Add to Slack", for this organization.

    Answers with Slack's consent page for Lemma's own app, asking for its bot
    alone. Refused before anything is made when Slack isn't set up here, or
    the caller may not add the organization's installs. When the grant comes
    back to this same signed-in browser, the workspace becomes the
    organization's and Lemma's app says hello there; a grant finished anywhere
    else adopts nothing."""
    url = await start_slack_install(
        uow, user_id=user.id, organization_id=request.organization_id
    )
    return SlackInstallResponse(authorization_url=url)


@people_router.post(
    "/people/sign-in", operation_id="agent.surface.slack_person.sign_in"
)
async def start_slack_sign_in(
    request: SlackSignInRequest, user: CurrentUser, uow: UoWDep
) -> SlackSignInResponse:
    """Start Sign in with Slack for the signed-in person, to prove they are the
    Slack user their App Home's link was for.

    Connects nothing. The callback connects only when it sees this same Lemma
    session and Slack names this same Slack user and workspace.
    """
    url = await start_sign_in(
        uow,
        user_id=user.id,
        team_id=request.team_id,
        slack_user_id=request.slack_user_id,
    )
    return SlackSignInResponse(authorization_url=url)


def _back_to_page(**query: str) -> RedirectResponse:
    target = f"{settings.frontend_url.rstrip('/')}{_CONNECT_PAGE}?{urlencode(query)}"
    return RedirectResponse(target, status_code=303)


@people_router.get(
    CALLBACK_PATH.removeprefix("/surface-setup/slack"),
    include_in_schema=False,
)
async def finish_slack_sign_in(
    session_user_id: BrowserUserId,
    uow: UoWDep,
    state: str = Query(min_length=20, max_length=256),
    code: str | None = Query(default=None, min_length=1, max_length=4096),
    error: str | None = Query(default=None, max_length=255),
) -> RedirectResponse:
    """Slack sending the browser back: check, record, and return to the page.

    Exempt from the session gate, which would answer a browser that lost its
    session with a bare 401: the session is only read here, and one that is
    missing or another person's is refused as ``session_changed``, on the page.
    """
    try:
        proven = await finish_sign_in(
            uow,
            state=state,
            code=None if error else code,
            session_user_id=session_user_id,
        )
    except SlackSignInError as refused:
        return _back_to_page(error=refused.reason)
    # Their Home was the "connect" view; show them their teammates instead.
    # The proof is committed, so a Home that fails to refresh still lands them
    # on "connected"; it refreshes the next time they open it.
    await refresh_home_of(
        uow, team_id=proven.team_id, slack_user_id=proven.slack_user_id
    )
    return _back_to_page(connected="1")

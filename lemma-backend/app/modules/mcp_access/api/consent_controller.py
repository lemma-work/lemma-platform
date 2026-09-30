"""Routes a signed-in person uses: answering a consent request, and managing
the clients they have connected.

Both need the person's own session, which is why they are ordinary API routes
under the global auth gate rather than beside the public OAuth endpoints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import CurrentUser, get_uow_factory
from app.core.api.schemas import ErrorResponse
from app.modules.mcp_access.domain.entities import ConnectedApp, Scope
from app.modules.mcp_access.domain.resources import pod_resource_url
from app.modules.mcp_access.services.grants import GrantService
from app.modules.mcp_access.services.wiring import consent_service, issuer

TAG = "MCP Access"

router = APIRouter(prefix="/oauth", tags=[TAG])


class ConsentRequestResponse(BaseModel):
    client_id: str
    client_name: str = Field(description="As the client names itself. Unverified.")
    verified_host: str | None = Field(
        default=None,
        description=(
            "The host serving the client's metadata document -- the one checked "
            "fact about who is asking. Null for a dynamically registered client, "
            "about which nothing is checked."
        ),
    )
    client_uri: str | None = None
    redirect_host: str = Field(
        description=(
            "Where the person is sent back to: a web redirect's host, or an "
            "app's scheme and a colon (`cursor:`), since the rest of an app's "
            "URI proves nothing. The one part of the request a client cannot "
            "claim falsely, so the consent screen shows it."
        )
    )
    redirect_to_app: bool = Field(
        default=False,
        description="The redirect opens an app on the device, not a web page.",
    )
    pod_id: UUID
    pod_name: str
    scopes: list[Scope]


class ConsentAnswerRequest(BaseModel):
    allow: bool
    read_only: bool = Field(
        default=False,
        description="Allow reading only, whatever the client asked for.",
    )


class ConsentAnswerResponse(BaseModel):
    redirect_to: str = Field(
        description="The client's redirect URI with the code, or with access_denied."
    )


class ConnectedClientResponse(BaseModel):
    grant_id: UUID
    user_id: UUID = Field(description="The person who connected it.")
    pod_id: UUID
    client_id: str
    client_name: str
    client_uri: str | None
    scopes: list[Scope]
    connected_at: datetime
    last_used_at: datetime | None


class ConnectedClientsResponse(BaseModel):
    items: list[ConnectedClientResponse]


class McpEndpointResponse(BaseModel):
    url: str = Field(description="The URL to add to an MCP client for this pod.")


def _connected(app: ConnectedApp) -> ConnectedClientResponse:
    return ConnectedClientResponse(
        grant_id=app.grant_id,
        user_id=app.user_id,
        pod_id=app.pod_id,
        client_id=app.client_id,
        client_name=app.client_name,
        client_uri=app.client_uri,
        scopes=sorted(app.scopes),
        connected_at=app.created_at,
        last_used_at=app.last_used_at,
    )


# The consent routes are a contract with the auth portal's `/auth/authorize`
# page and nothing else -- the same standing as the CLI's session routes -- so
# they stay out of the public schema and the SDKs generated from it.
@router.get(
    "/consent/{request_id}",
    include_in_schema=False,
    response_model=ConsentRequestResponse,
    operation_id="mcp_access.consent.get",
    summary="What an MCP client is asking for",
)
async def get_consent_request(
    request_id: str, user: CurrentUser
) -> ConsentRequestResponse:
    found = await consent_service().describe(request_id=request_id, user_id=user.id)
    return ConsentRequestResponse(
        client_id=found.client_id,
        client_name=found.client_name,
        verified_host=found.verified_host,
        client_uri=found.client_uri,
        redirect_host=found.redirect_host,
        redirect_to_app=found.redirect_to_app,
        pod_id=found.pod_id,
        pod_name=found.pod_name,
        scopes=sorted(found.scopes),
    )


@router.post(
    "/consent/{request_id}",
    include_in_schema=False,
    response_model=ConsentAnswerResponse,
    operation_id="mcp_access.consent.answer",
    summary="Allow or deny an MCP client",
)
async def answer_consent_request(
    request_id: str, body: ConsentAnswerRequest, user: CurrentUser
) -> ConsentAnswerResponse:
    redirect_to = await consent_service().answer(
        request_id=request_id,
        user_id=user.id,
        allow=body.allow,
        read_only=body.read_only,
    )
    return ConsentAnswerResponse(redirect_to=redirect_to)


@router.get(
    "/grants",
    response_model=ConnectedClientsResponse,
    operation_id="mcp_access.grants.list",
    summary="MCP clients you have connected",
)
async def list_grants(
    user: CurrentUser,
    pod_id: UUID | None = Query(default=None, description="Only this pod's."),
    everyone: bool = Query(
        default=False,
        description="Every member's connections to pod_id. Pod admins only.",
    ),
) -> ConnectedClientsResponse:
    apps = await GrantService(get_uow_factory()).list(
        user_id=user.id, pod_id=pod_id, everyone=everyone
    )
    return ConnectedClientsResponse(items=[_connected(app) for app in apps])


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="mcp_access.grants.revoke",
    summary="Disconnect an MCP client",
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No such connection, already ended, or not yours to end",
        }
    },
)
async def revoke_grant(grant_id: UUID, user: CurrentUser) -> None:
    """Ends the grant and every token it issued. The client's next request is
    refused and it has to ask the person again. A pod's admins may end any
    member's connection to their pod."""
    if not await GrantService(get_uow_factory()).revoke(
        user_id=user.id, grant_id=grant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such connection"
        )


@router.get(
    "/mcp-endpoint/{pod_id}",
    response_model=McpEndpointResponse,
    operation_id="mcp_access.endpoint.get",
    summary="The MCP URL for a pod",
)
async def get_mcp_endpoint(pod_id: UUID, user: CurrentUser) -> McpEndpointResponse:
    """Built here rather than in the browser, which knows the API's address
    only as the page was configured -- not necessarily as clients reach it."""
    del user
    return McpEndpointResponse(url=pod_resource_url(issuer(), pod_id))

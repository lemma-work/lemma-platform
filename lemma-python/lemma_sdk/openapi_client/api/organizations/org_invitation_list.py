from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.organization_invitation_list_response import (
    OrganizationInvitationListResponse,
)
from ...models.organization_invitation_status import OrganizationInvitationStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    status: OrganizationInvitationStatus | Unset = UNSET,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: str | Unset = UNSET
    if not isinstance(status, Unset):
        json_status = status.value

    params["status"] = json_status

    params["limit"] = limit

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{organization_id}/invitations".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OrganizationInvitationListResponse | None:
    if response.status_code == 200:
        response_200 = OrganizationInvitationListResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | OrganizationInvitationListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: OrganizationInvitationStatus | Unset = UNSET,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OrganizationInvitationListResponse]:
    """List Organization Invitations

     Get all pending invitations for an organization

    Args:
        organization_id (UUID):
        status (OrganizationInvitationStatus | Unset): Statuses for organization invitations.
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationInvitationListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        status=status,
        limit=limit,
        page_token=page_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: OrganizationInvitationStatus | Unset = UNSET,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | OrganizationInvitationListResponse | None:
    """List Organization Invitations

     Get all pending invitations for an organization

    Args:
        organization_id (UUID):
        status (OrganizationInvitationStatus | Unset): Statuses for organization invitations.
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationInvitationListResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        status=status,
        limit=limit,
        page_token=page_token,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: OrganizationInvitationStatus | Unset = UNSET,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | OrganizationInvitationListResponse]:
    """List Organization Invitations

     Get all pending invitations for an organization

    Args:
        organization_id (UUID):
        status (OrganizationInvitationStatus | Unset): Statuses for organization invitations.
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationInvitationListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        status=status,
        limit=limit,
        page_token=page_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: OrganizationInvitationStatus | Unset = UNSET,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | OrganizationInvitationListResponse | None:
    """List Organization Invitations

     Get all pending invitations for an organization

    Args:
        organization_id (UUID):
        status (OrganizationInvitationStatus | Unset): Statuses for organization invitations.
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationInvitationListResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            status=status,
            limit=limit,
            page_token=page_token,
        )
    ).parsed

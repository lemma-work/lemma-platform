from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.organization_member_response import OrganizationMemberResponse
from ...models.update_member_role_request import UpdateMemberRoleRequest
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    member_id: UUID,
    *,
    body: UpdateMemberRoleRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/organizations/{organization_id}/members/{member_id}/role".format(
            organization_id=quote(str(organization_id), safe=""),
            member_id=quote(str(member_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OrganizationMemberResponse | None:
    if response.status_code == 200:
        response_200 = OrganizationMemberResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OrganizationMemberResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    member_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateMemberRoleRequest,
) -> Response[ErrorResponse | OrganizationMemberResponse]:
    """Update Member Role

     Update a member's role in the organization

    Args:
        organization_id (UUID):
        member_id (UUID):
        body (UpdateMemberRoleRequest): Update member role request schema.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationMemberResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        member_id=member_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    member_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateMemberRoleRequest,
) -> ErrorResponse | OrganizationMemberResponse | None:
    """Update Member Role

     Update a member's role in the organization

    Args:
        organization_id (UUID):
        member_id (UUID):
        body (UpdateMemberRoleRequest): Update member role request schema.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationMemberResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        member_id=member_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    member_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateMemberRoleRequest,
) -> Response[ErrorResponse | OrganizationMemberResponse]:
    """Update Member Role

     Update a member's role in the organization

    Args:
        organization_id (UUID):
        member_id (UUID):
        body (UpdateMemberRoleRequest): Update member role request schema.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationMemberResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        member_id=member_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    member_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateMemberRoleRequest,
) -> ErrorResponse | OrganizationMemberResponse | None:
    """Update Member Role

     Update a member's role in the organization

    Args:
        organization_id (UUID):
        member_id (UUID):
        body (UpdateMemberRoleRequest): Update member role request schema.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationMemberResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            member_id=member_id,
            client=client,
            body=body,
        )
    ).parsed

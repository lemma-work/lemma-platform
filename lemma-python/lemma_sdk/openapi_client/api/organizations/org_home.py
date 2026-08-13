from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.organization_home_response import OrganizationHomeResponse
from ...types import Response


def _get_kwargs(
    org_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{org_id}/home".format(
            org_id=quote(str(org_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OrganizationHomeResponse | None:
    if response.status_code == 200:
        response_200 = OrganizationHomeResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OrganizationHomeResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | OrganizationHomeResponse]:
    """Get Organization Home

     One organization's landing page: every pod the current user can see, with its apps, its agents, and
    the user's roles in that pod. Replaces fetching apps and agents per pod. Cached briefly per user.

    Args:
        org_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationHomeResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | OrganizationHomeResponse | None:
    """Get Organization Home

     One organization's landing page: every pod the current user can see, with its apps, its agents, and
    the user's roles in that pod. Replaces fetching apps and agents per pod. Cached briefly per user.

    Args:
        org_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationHomeResponse
    """

    return sync_detailed(
        org_id=org_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | OrganizationHomeResponse]:
    """Get Organization Home

     One organization's landing page: every pod the current user can see, with its apps, its agents, and
    the user's roles in that pod. Replaces fetching apps and agents per pod. Cached briefly per user.

    Args:
        org_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationHomeResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | OrganizationHomeResponse | None:
    """Get Organization Home

     One organization's landing page: every pod the current user can see, with its apps, its agents, and
    the user's roles in that pod. Replaces fetching apps and agents per pod. Cached briefly per user.

    Args:
        org_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationHomeResponse
    """

    return (
        await asyncio_detailed(
            org_id=org_id,
            client=client,
        )
    ).parsed

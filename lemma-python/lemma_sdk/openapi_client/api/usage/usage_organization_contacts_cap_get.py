from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.contacts_cap_response import ContactsCapResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/usage/organizations/{organization_id}/contacts-cap".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ContactsCapResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ContactsCapResponse.from_dict(response.json())

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
) -> Response[ContactsCapResponse | ErrorResponse]:
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
) -> Response[ContactsCapResponse | ErrorResponse]:
    """Get Contacts Cap

     The organization's cap on what answering contacts may cost a month.

    Args:
        organization_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContactsCapResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ContactsCapResponse | ErrorResponse | None:
    """Get Contacts Cap

     The organization's cap on what answering contacts may cost a month.

    Args:
        organization_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContactsCapResponse | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ContactsCapResponse | ErrorResponse]:
    """Get Contacts Cap

     The organization's cap on what answering contacts may cost a month.

    Args:
        organization_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ContactsCapResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ContactsCapResponse | ErrorResponse | None:
    """Get Contacts Cap

     The organization's cap on what answering contacts may cost a month.

    Args:
        organization_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ContactsCapResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
        )
    ).parsed

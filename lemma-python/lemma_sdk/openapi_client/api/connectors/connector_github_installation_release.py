from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.message_response_schema import MessageResponseSchema
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    installation_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/organizations/{organization_id}/connectors/github/installations/{installation_id}".format(
            organization_id=quote(str(organization_id), safe=""),
            installation_id=quote(str(installation_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MessageResponseSchema | None:
    if response.status_code == 200:
        response_200 = MessageResponseSchema.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | MessageResponseSchema]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    installation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | MessageResponseSchema]:
    """Stop Using A GitHub Installation

     This organization lets go of a GitHub App installation it holds. Other organizations holding the
    same installation keep theirs. Its accounts naming the installation forget it, and its schedules on
    it are switched off, with the reason. Reading an account does not take it back; picking it on an
    account, or connecting through it, does. Needs `org.installation.manage`.

    Args:
        organization_id (UUID):
        installation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MessageResponseSchema]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        installation_id=installation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    installation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | MessageResponseSchema | None:
    """Stop Using A GitHub Installation

     This organization lets go of a GitHub App installation it holds. Other organizations holding the
    same installation keep theirs. Its accounts naming the installation forget it, and its schedules on
    it are switched off, with the reason. Reading an account does not take it back; picking it on an
    account, or connecting through it, does. Needs `org.installation.manage`.

    Args:
        organization_id (UUID):
        installation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MessageResponseSchema
    """

    return sync_detailed(
        organization_id=organization_id,
        installation_id=installation_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    installation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | MessageResponseSchema]:
    """Stop Using A GitHub Installation

     This organization lets go of a GitHub App installation it holds. Other organizations holding the
    same installation keep theirs. Its accounts naming the installation forget it, and its schedules on
    it are switched off, with the reason. Reading an account does not take it back; picking it on an
    account, or connecting through it, does. Needs `org.installation.manage`.

    Args:
        organization_id (UUID):
        installation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MessageResponseSchema]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        installation_id=installation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    installation_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | MessageResponseSchema | None:
    """Stop Using A GitHub Installation

     This organization lets go of a GitHub App installation it holds. Other organizations holding the
    same installation keep theirs. Its accounts naming the installation forget it, and its schedules on
    it are switched off, with the reason. Reading an account does not take it back; picking it on an
    account, or connecting through it, does. Needs `org.installation.manage`.

    Args:
        organization_id (UUID):
        installation_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MessageResponseSchema
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            installation_id=installation_id,
            client=client,
        )
    ).parsed

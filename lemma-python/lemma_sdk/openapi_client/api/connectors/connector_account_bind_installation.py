from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.account_response_schema import AccountResponseSchema
from ...models.error_response import ErrorResponse
from ...models.installation_bind_schema import InstallationBindSchema
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    account_id: UUID,
    *,
    body: InstallationBindSchema,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/organizations/{organization_id}/connectors/accounts/{account_id}/github/installations".format(
            organization_id=quote(str(organization_id), safe=""),
            account_id=quote(str(account_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AccountResponseSchema | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AccountResponseSchema.from_dict(response.json())

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
) -> Response[AccountResponseSchema | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationBindSchema,
) -> Response[AccountResponseSchema | ErrorResponse]:
    """Bind Account Installation

     Bind an account to one of the installations it can reach.

    Args:
        organization_id (UUID):
        account_id (UUID):
        body (InstallationBindSchema): Which of several installations this account speaks for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountResponseSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        account_id=account_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationBindSchema,
) -> AccountResponseSchema | ErrorResponse | None:
    """Bind Account Installation

     Bind an account to one of the installations it can reach.

    Args:
        organization_id (UUID):
        account_id (UUID):
        body (InstallationBindSchema): Which of several installations this account speaks for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountResponseSchema | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        account_id=account_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationBindSchema,
) -> Response[AccountResponseSchema | ErrorResponse]:
    """Bind Account Installation

     Bind an account to one of the installations it can reach.

    Args:
        organization_id (UUID):
        account_id (UUID):
        body (InstallationBindSchema): Which of several installations this account speaks for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountResponseSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        account_id=account_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: InstallationBindSchema,
) -> AccountResponseSchema | ErrorResponse | None:
    """Bind Account Installation

     Bind an account to one of the installations it can reach.

    Args:
        organization_id (UUID):
        account_id (UUID):
        body (InstallationBindSchema): Which of several installations this account speaks for.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountResponseSchema | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            account_id=account_id,
            client=client,
            body=body,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.account_installations_schema import AccountInstallationsSchema
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    account_id: UUID,
    *,
    refresh: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["refresh"] = refresh

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{organization_id}/connectors/accounts/{account_id}/github/installations".format(
            organization_id=quote(str(organization_id), safe=""),
            account_id=quote(str(account_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AccountInstallationsSchema | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AccountInstallationsSchema.from_dict(response.json())

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
) -> Response[AccountInstallationsSchema | ErrorResponse]:
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
    refresh: bool | Unset = False,
) -> Response[AccountInstallationsSchema | ErrorResponse]:
    """Account Installations

     Which GitHub App installations this account can reach, resolving and recording one when it is
    unambiguous.

    Args:
        organization_id (UUID):
        account_id (UUID):
        refresh (bool | Unset): Ask the provider again rather than trusting what is recorded.
            Editing an installation's repositories sends no callback and no reliable event, so this is
            how a change made on GitHub is seen. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountInstallationsSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        account_id=account_id,
        refresh=refresh,
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
    refresh: bool | Unset = False,
) -> AccountInstallationsSchema | ErrorResponse | None:
    """Account Installations

     Which GitHub App installations this account can reach, resolving and recording one when it is
    unambiguous.

    Args:
        organization_id (UUID):
        account_id (UUID):
        refresh (bool | Unset): Ask the provider again rather than trusting what is recorded.
            Editing an installation's repositories sends no callback and no reliable event, so this is
            how a change made on GitHub is seen. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountInstallationsSchema | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        account_id=account_id,
        client=client,
        refresh=refresh,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    refresh: bool | Unset = False,
) -> Response[AccountInstallationsSchema | ErrorResponse]:
    """Account Installations

     Which GitHub App installations this account can reach, resolving and recording one when it is
    unambiguous.

    Args:
        organization_id (UUID):
        account_id (UUID):
        refresh (bool | Unset): Ask the provider again rather than trusting what is recorded.
            Editing an installation's repositories sends no callback and no reliable event, so this is
            how a change made on GitHub is seen. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AccountInstallationsSchema | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        account_id=account_id,
        refresh=refresh,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    account_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    refresh: bool | Unset = False,
) -> AccountInstallationsSchema | ErrorResponse | None:
    """Account Installations

     Which GitHub App installations this account can reach, resolving and recording one when it is
    unambiguous.

    Args:
        organization_id (UUID):
        account_id (UUID):
        refresh (bool | Unset): Ask the provider again rather than trusting what is recorded.
            Editing an installation's repositories sends no callback and no reliable event, so this is
            how a change made on GitHub is seen. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AccountInstallationsSchema | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            account_id=account_id,
            client=client,
            refresh=refresh,
        )
    ).parsed

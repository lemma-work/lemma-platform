from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_runtime_profile_list_response import (
    AgentRuntimeProfileListResponse,
)
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    include_disabled: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["include_disabled"] = include_disabled

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{organization_id}/agent-runtime/profiles".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentRuntimeProfileListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentRuntimeProfileListResponse.from_dict(response.json())

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
) -> Response[AgentRuntimeProfileListResponse | ErrorResponse]:
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
    include_disabled: bool | Unset = False,
) -> Response[AgentRuntimeProfileListResponse | ErrorResponse]:
    """List Available Agent Runtime Profiles

    Args:
        organization_id (UUID):
        include_disabled (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        include_disabled=include_disabled,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    include_disabled: bool | Unset = False,
) -> AgentRuntimeProfileListResponse | ErrorResponse | None:
    """List Available Agent Runtime Profiles

    Args:
        organization_id (UUID):
        include_disabled (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileListResponse | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        include_disabled=include_disabled,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    include_disabled: bool | Unset = False,
) -> Response[AgentRuntimeProfileListResponse | ErrorResponse]:
    """List Available Agent Runtime Profiles

    Args:
        organization_id (UUID):
        include_disabled (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        include_disabled=include_disabled,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    include_disabled: bool | Unset = False,
) -> AgentRuntimeProfileListResponse | ErrorResponse | None:
    """List Available Agent Runtime Profiles

    Args:
        organization_id (UUID):
        include_disabled (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            include_disabled=include_disabled,
        )
    ).parsed

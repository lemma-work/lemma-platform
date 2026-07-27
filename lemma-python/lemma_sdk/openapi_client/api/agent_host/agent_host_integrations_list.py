from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_host_integration_list_response import (
    AgentHostIntegrationListResponse,
)
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    host_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/me/agent-hosts/{host_id}/integrations".format(
            host_id=quote(str(host_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentHostIntegrationListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentHostIntegrationListResponse.from_dict(response.json())

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
) -> Response[AgentHostIntegrationListResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    host_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentHostIntegrationListResponse | ErrorResponse]:
    """List Agent Host Integrations

    Args:
        host_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentHostIntegrationListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        host_id=host_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    host_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AgentHostIntegrationListResponse | ErrorResponse | None:
    """List Agent Host Integrations

    Args:
        host_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentHostIntegrationListResponse | ErrorResponse
    """

    return sync_detailed(
        host_id=host_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    host_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentHostIntegrationListResponse | ErrorResponse]:
    """List Agent Host Integrations

    Args:
        host_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentHostIntegrationListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        host_id=host_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    host_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AgentHostIntegrationListResponse | ErrorResponse | None:
    """List Agent Host Integrations

    Args:
        host_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentHostIntegrationListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            host_id=host_id,
            client=client,
        )
    ).parsed

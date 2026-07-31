from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_surface_list_response import AgentSurfaceListResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    platform: None | str | Unset = UNSET,
    agent_name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    json_platform: None | str | Unset
    if isinstance(platform, Unset):
        json_platform = UNSET
    else:
        json_platform = platform
    params["platform"] = json_platform

    json_agent_name: None | str | Unset
    if isinstance(agent_name, Unset):
        json_agent_name = UNSET
    else:
        json_agent_name = agent_name
    params["agent_name"] = json_agent_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/surfaces".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentSurfaceListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentSurfaceListResponse.from_dict(response.json())

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
) -> Response[AgentSurfaceListResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    platform: None | str | Unset = UNSET,
    agent_name: None | str | Unset = UNSET,
) -> Response[AgentSurfaceListResponse | ErrorResponse]:
    """List Surfaces

     List surfaces in the pod. A pod may have several surfaces of the same
    ``platform`` (different bots/accounts, one per agent); filter by
    ``platform`` and/or ``agent_name`` to narrow the results.

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        platform (None | str | Unset):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        page_token=page_token,
        platform=platform,
        agent_name=agent_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    platform: None | str | Unset = UNSET,
    agent_name: None | str | Unset = UNSET,
) -> AgentSurfaceListResponse | ErrorResponse | None:
    """List Surfaces

     List surfaces in the pod. A pod may have several surfaces of the same
    ``platform`` (different bots/accounts, one per agent); filter by
    ``platform`` and/or ``agent_name`` to narrow the results.

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        platform (None | str | Unset):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        limit=limit,
        page_token=page_token,
        platform=platform,
        agent_name=agent_name,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    platform: None | str | Unset = UNSET,
    agent_name: None | str | Unset = UNSET,
) -> Response[AgentSurfaceListResponse | ErrorResponse]:
    """List Surfaces

     List surfaces in the pod. A pod may have several surfaces of the same
    ``platform`` (different bots/accounts, one per agent); filter by
    ``platform`` and/or ``agent_name`` to narrow the results.

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        platform (None | str | Unset):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        page_token=page_token,
        platform=platform,
        agent_name=agent_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    platform: None | str | Unset = UNSET,
    agent_name: None | str | Unset = UNSET,
) -> AgentSurfaceListResponse | ErrorResponse | None:
    """List Surfaces

     List surfaces in the pod. A pod may have several surfaces of the same
    ``platform`` (different bots/accounts, one per agent); filter by
    ``platform`` and/or ``agent_name`` to narrow the results.

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        platform (None | str | Unset):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            limit=limit,
            page_token=page_token,
            platform=platform,
            agent_name=agent_name,
        )
    ).parsed

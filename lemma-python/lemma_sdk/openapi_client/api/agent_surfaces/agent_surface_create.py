from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_surface_response import AgentSurfaceResponse
from ...models.error_response import ErrorResponse
from ...models.surface_create_request import SurfaceCreateRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: SurfaceCreateRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/surfaces".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentSurfaceResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentSurfaceResponse.from_dict(response.json())

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
) -> Response[AgentSurfaceResponse | ErrorResponse]:
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
    body: SurfaceCreateRequest,
) -> Response[AgentSurfaceResponse | ErrorResponse]:
    """Create Surface

     Create a surface. ``name`` defaults to the lowercased platform — pass an
    explicit name to create a second surface of the same platform (e.g. a
    second bot routed to a different agent).

    Args:
        pod_id (UUID):
        body (SurfaceCreateRequest): Body for `POST /pods/{pod_id}/surfaces` — creates one
            surface.

            A pod may have several surfaces of the same ``platform`` (different
            bots/accounts, each routed to its own agent); ``name`` is the stable,
            pod-unique identifier used to address it afterward. When omitted, it
            defaults to the lowercased platform (so the common single-surface-per-
            platform case needs no name at all) — pick an explicit name to create a
            second surface of the same platform.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceCreateRequest,
) -> AgentSurfaceResponse | ErrorResponse | None:
    """Create Surface

     Create a surface. ``name`` defaults to the lowercased platform — pass an
    explicit name to create a second surface of the same platform (e.g. a
    second bot routed to a different agent).

    Args:
        pod_id (UUID):
        body (SurfaceCreateRequest): Body for `POST /pods/{pod_id}/surfaces` — creates one
            surface.

            A pod may have several surfaces of the same ``platform`` (different
            bots/accounts, each routed to its own agent); ``name`` is the stable,
            pod-unique identifier used to address it afterward. When omitted, it
            defaults to the lowercased platform (so the common single-surface-per-
            platform case needs no name at all) — pick an explicit name to create a
            second surface of the same platform.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceCreateRequest,
) -> Response[AgentSurfaceResponse | ErrorResponse]:
    """Create Surface

     Create a surface. ``name`` defaults to the lowercased platform — pass an
    explicit name to create a second surface of the same platform (e.g. a
    second bot routed to a different agent).

    Args:
        pod_id (UUID):
        body (SurfaceCreateRequest): Body for `POST /pods/{pod_id}/surfaces` — creates one
            surface.

            A pod may have several surfaces of the same ``platform`` (different
            bots/accounts, each routed to its own agent); ``name`` is the stable,
            pod-unique identifier used to address it afterward. When omitted, it
            defaults to the lowercased platform (so the common single-surface-per-
            platform case needs no name at all) — pick an explicit name to create a
            second surface of the same platform.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceCreateRequest,
) -> AgentSurfaceResponse | ErrorResponse | None:
    """Create Surface

     Create a surface. ``name`` defaults to the lowercased platform — pass an
    explicit name to create a second surface of the same platform (e.g. a
    second bot routed to a different agent).

    Args:
        pod_id (UUID):
        body (SurfaceCreateRequest): Body for `POST /pods/{pod_id}/surfaces` — creates one
            surface.

            A pod may have several surfaces of the same ``platform`` (different
            bots/accounts, each routed to its own agent); ``name`` is the stable,
            pod-unique identifier used to address it afterward. When omitted, it
            defaults to the lowercased platform (so the common single-surface-per-
            platform case needs no name at all) — pick an explicit name to create a
            second surface of the same platform.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

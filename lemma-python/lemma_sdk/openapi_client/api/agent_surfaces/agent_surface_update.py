from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_surface_response import AgentSurfaceResponse
from ...models.error_response import ErrorResponse
from ...models.surface_update_request import SurfaceUpdateRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    surface_name: str,
    *,
    body: SurfaceUpdateRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/surfaces/{surface_name}".format(
            pod_id=quote(str(pod_id), safe=""),
            surface_name=quote(str(surface_name), safe=""),
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
    surface_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceUpdateRequest,
) -> Response[AgentSurfaceResponse | ErrorResponse]:
    """Update Surface

     Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceUpdateRequest): Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

            Partial update (merge semantics): only fields present in the request are
            applied. The surface's ``platform`` and ``name`` are immutable — delete and
            recreate to change either.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        surface_name=surface_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    surface_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceUpdateRequest,
) -> AgentSurfaceResponse | ErrorResponse | None:
    """Update Surface

     Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceUpdateRequest): Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

            Partial update (merge semantics): only fields present in the request are
            applied. The surface's ``platform`` and ``name`` are immutable — delete and
            recreate to change either.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        surface_name=surface_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    surface_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceUpdateRequest,
) -> Response[AgentSurfaceResponse | ErrorResponse]:
    """Update Surface

     Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceUpdateRequest): Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

            Partial update (merge semantics): only fields present in the request are
            applied. The surface's ``platform`` and ``name`` are immutable — delete and
            recreate to change either.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        surface_name=surface_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    surface_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: SurfaceUpdateRequest,
) -> AgentSurfaceResponse | ErrorResponse | None:
    """Update Surface

     Partially update a surface. Only fields present in the request are
    applied; the surface's platform and name are immutable.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceUpdateRequest): Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.

            Partial update (merge semantics): only fields present in the request are
            applied. The surface's ``platform`` and ``name`` are immutable — delete and
            recreate to change either.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            surface_name=surface_name,
            client=client,
            body=body,
        )
    ).parsed

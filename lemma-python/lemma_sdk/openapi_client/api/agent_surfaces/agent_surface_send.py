from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.surface_send_request import SurfaceSendRequest
from ...models.surface_send_response import SurfaceSendResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    surface_name: str,
    *,
    body: SurfaceSendRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/surfaces/{surface_name}/send".format(
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
) -> ErrorResponse | SurfaceSendResponse | None:
    if response.status_code == 200:
        response_200 = SurfaceSendResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SurfaceSendResponse]:
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
    body: SurfaceSendRequest,
) -> Response[ErrorResponse | SurfaceSendResponse]:
    """Send Surface Message

     Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceSendRequest): Send a proactive message to a pod member on this surface.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SurfaceSendResponse]
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
    body: SurfaceSendRequest,
) -> ErrorResponse | SurfaceSendResponse | None:
    """Send Surface Message

     Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceSendRequest): Send a proactive message to a pod member on this surface.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SurfaceSendResponse
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
    body: SurfaceSendRequest,
) -> Response[ErrorResponse | SurfaceSendResponse]:
    """Send Surface Message

     Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceSendRequest): Send a proactive message to a pod member on this surface.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SurfaceSendResponse]
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
    body: SurfaceSendRequest,
) -> ErrorResponse | SurfaceSendResponse | None:
    """Send Surface Message

     Proactively send a message to a pod member on this surface.

    Powers notifications from functions/workflows. Reuses the member's existing
    thread on the surface (bots can't cold-DM), so a 404 means the member has no
    reachable conversation here yet.

    Args:
        pod_id (UUID):
        surface_name (str):
        body (SurfaceSendRequest): Send a proactive message to a pod member on this surface.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SurfaceSendResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            surface_name=surface_name,
            client=client,
            body=body,
        )
    ).parsed

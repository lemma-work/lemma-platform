from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.surface_platform_setup_guide import SurfacePlatformSetupGuide
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    platform: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/surface-setup/{platform}".format(
            pod_id=quote(str(pod_id), safe=""),
            platform=quote(str(platform), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SurfacePlatformSetupGuide | None:
    if response.status_code == 200:
        response_200 = SurfacePlatformSetupGuide.from_dict(response.json())

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
) -> Response[ErrorResponse | SurfacePlatformSetupGuide]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SurfacePlatformSetupGuide]:
    """Get Surface Setup Guide

     The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists.

    Args:
        pod_id (UUID):
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SurfacePlatformSetupGuide]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        platform=platform,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SurfacePlatformSetupGuide | None:
    """Get Surface Setup Guide

     The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists.

    Args:
        pod_id (UUID):
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SurfacePlatformSetupGuide
    """

    return sync_detailed(
        pod_id=pod_id,
        platform=platform,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SurfacePlatformSetupGuide]:
    """Get Surface Setup Guide

     The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists.

    Args:
        pod_id (UUID):
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SurfacePlatformSetupGuide]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        platform=platform,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    platform: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SurfacePlatformSetupGuide | None:
    """Get Surface Setup Guide

     The static pre-creation checklist for a platform (env/OAuth
    prerequisites) — works before any surface of this platform exists.

    Args:
        pod_id (UUID):
        platform (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SurfacePlatformSetupGuide
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            platform=platform,
            client=client,
        )
    ).parsed

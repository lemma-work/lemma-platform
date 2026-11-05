from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.source_settings_detail_response import SourceSettingsDetailResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels/{channel_id}/sources/{source_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
            source_id=quote(str(source_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SourceSettingsDetailResponse | None:
    if response.status_code == 200:
        response_200 = SourceSettingsDetailResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SourceSettingsDetailResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SourceSettingsDetailResponse]:
    """Get Source

     A group source's settings: who answers its outsiders, and from what.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        source_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceSettingsDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        source_id=source_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SourceSettingsDetailResponse | None:
    """Get Source

     A group source's settings: who answers its outsiders, and from what.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        source_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceSettingsDetailResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        source_id=source_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SourceSettingsDetailResponse]:
    """Get Source

     A group source's settings: who answers its outsiders, and from what.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        source_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SourceSettingsDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        source_id=source_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    source_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SourceSettingsDetailResponse | None:
    """Get Source

     A group source's settings: who answers its outsiders, and from what.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        source_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SourceSettingsDetailResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            source_id=source_id,
            client=client,
        )
    ).parsed

from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    publish_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/bundle/publishes/{publish_id}/events".format(
            pod_id=quote(str(pod_id), safe=""),
            publish_id=quote(str(publish_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = cast(Any, None)
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
) -> Response[Any | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    publish_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Stream Pod Publish Progress

     Server-Sent Events for a publish (snapshot then live frames).

    Args:
        pod_id (UUID):
        publish_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        publish_id=publish_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    publish_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Stream Pod Publish Progress

     Server-Sent Events for a publish (snapshot then live frames).

    Args:
        pod_id (UUID):
        publish_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        publish_id=publish_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    publish_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Stream Pod Publish Progress

     Server-Sent Events for a publish (snapshot then live frames).

    Args:
        pod_id (UUID):
        publish_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        publish_id=publish_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    publish_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Stream Pod Publish Progress

     Server-Sent Events for a publish (snapshot then live frames).

    Args:
        pod_id (UUID):
        publish_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            publish_id=publish_id,
            client=client,
        )
    ).parsed

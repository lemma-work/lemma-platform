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
    channel_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/pods/{pod_id}/channels/{channel_id}/members/me".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

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
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Leave Channel

     Leave a channel. Leaving one not joined is nothing; a member's own
    channel cannot be left, nor a private group by the last person in it (409
    `LAST_MEMBER`). A group's last owner leaving hands it to whoever has been
    in it longest.

    Args:
        pod_id (UUID):
        channel_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Leave Channel

     Leave a channel. Leaving one not joined is nothing; a member's own
    channel cannot be left, nor a private group by the last person in it (409
    `LAST_MEMBER`). A group's last owner leaving hands it to whoever has been
    in it longest.

    Args:
        pod_id (UUID):
        channel_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Leave Channel

     Leave a channel. Leaving one not joined is nothing; a member's own
    channel cannot be left, nor a private group by the last person in it (409
    `LAST_MEMBER`). A group's last owner leaving hands it to whoever has been
    in it longest.

    Args:
        pod_id (UUID):
        channel_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Leave Channel

     Leave a channel. Leaving one not joined is nothing; a member's own
    channel cannot be left, nor a private group by the last person in it (409
    `LAST_MEMBER`). A group's last owner leaving hands it to whoever has been
    in it longest.

    Args:
        pod_id (UUID):
        channel_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            client=client,
        )
    ).parsed

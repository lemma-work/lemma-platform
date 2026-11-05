from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_message_response import ChannelMessageResponse
from ...models.channel_post_request import ChannelPostRequest
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    *,
    body: ChannelPostRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/channels/{channel_id}/messages".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChannelMessageResponse | ErrorResponse | None:
    if response.status_code == 201:
        response_201 = ChannelMessageResponse.from_dict(response.json())

        return response_201

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
) -> Response[ChannelMessageResponse | ErrorResponse]:
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
    body: ChannelPostRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ChannelMessageResponse | ErrorResponse]:
    """Post Message

     Write in the channel. A reply goes where its thread is; otherwise to
    the place named, or the one the channel last heard from -- unless it is a
    note to members. Naming somebody who cannot see the channel is refused.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        idempotency_key (None | str | Unset):
        body (ChannelPostRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelMessageResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        body=body,
        idempotency_key=idempotency_key,
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
    body: ChannelPostRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ChannelMessageResponse | ErrorResponse | None:
    """Post Message

     Write in the channel. A reply goes where its thread is; otherwise to
    the place named, or the one the channel last heard from -- unless it is a
    note to members. Naming somebody who cannot see the channel is refused.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        idempotency_key (None | str | Unset):
        body (ChannelPostRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelMessageResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelPostRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ChannelMessageResponse | ErrorResponse]:
    """Post Message

     Write in the channel. A reply goes where its thread is; otherwise to
    the place named, or the one the channel last heard from -- unless it is a
    note to members. Naming somebody who cannot see the channel is refused.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        idempotency_key (None | str | Unset):
        body (ChannelPostRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelMessageResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelPostRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ChannelMessageResponse | ErrorResponse | None:
    """Post Message

     Write in the channel. A reply goes where its thread is; otherwise to
    the place named, or the one the channel last heard from -- unless it is a
    note to members. Naming somebody who cannot see the channel is refused.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        idempotency_key (None | str | Unset):
        body (ChannelPostRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelMessageResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed

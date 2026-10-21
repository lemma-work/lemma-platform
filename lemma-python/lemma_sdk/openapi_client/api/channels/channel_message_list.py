from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_messages_response import ChannelMessagesResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    *,
    before_seq: int | None | Unset = UNSET,
    after_seq: int | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_before_seq: int | None | Unset
    if isinstance(before_seq, Unset):
        json_before_seq = UNSET
    else:
        json_before_seq = before_seq
    params["before_seq"] = json_before_seq

    json_after_seq: int | None | Unset
    if isinstance(after_seq, Unset):
        json_after_seq = UNSET
    else:
        json_after_seq = after_seq
    params["after_seq"] = json_after_seq

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels/{channel_id}/messages".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChannelMessagesResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ChannelMessagesResponse.from_dict(response.json())

        return response_200

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
) -> Response[ChannelMessagesResponse | ErrorResponse]:
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
    before_seq: int | None | Unset = UNSET,
    after_seq: int | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ChannelMessagesResponse | ErrorResponse]:
    """List Messages

     The timeline, oldest first: the newest page, the page before
    `before_seq`, or the page after `after_seq`. A thread's replies are not in
    it; its first line counts them.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        before_seq (int | None | Unset):
        after_seq (int | None | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelMessagesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        before_seq=before_seq,
        after_seq=after_seq,
        limit=limit,
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
    before_seq: int | None | Unset = UNSET,
    after_seq: int | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> ChannelMessagesResponse | ErrorResponse | None:
    """List Messages

     The timeline, oldest first: the newest page, the page before
    `before_seq`, or the page after `after_seq`. A thread's replies are not in
    it; its first line counts them.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        before_seq (int | None | Unset):
        after_seq (int | None | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelMessagesResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        client=client,
        before_seq=before_seq,
        after_seq=after_seq,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    before_seq: int | None | Unset = UNSET,
    after_seq: int | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ChannelMessagesResponse | ErrorResponse]:
    """List Messages

     The timeline, oldest first: the newest page, the page before
    `before_seq`, or the page after `after_seq`. A thread's replies are not in
    it; its first line counts them.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        before_seq (int | None | Unset):
        after_seq (int | None | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelMessagesResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        before_seq=before_seq,
        after_seq=after_seq,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    before_seq: int | None | Unset = UNSET,
    after_seq: int | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> ChannelMessagesResponse | ErrorResponse | None:
    """List Messages

     The timeline, oldest first: the newest page, the page before
    `before_seq`, or the page after `after_seq`. A thread's replies are not in
    it; its first line counts them.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        before_seq (int | None | Unset):
        after_seq (int | None | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelMessagesResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            client=client,
            before_seq=before_seq,
            after_seq=after_seq,
            limit=limit,
        )
    ).parsed

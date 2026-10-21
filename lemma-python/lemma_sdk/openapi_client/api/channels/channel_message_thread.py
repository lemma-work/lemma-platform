from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.thread_response import ThreadResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    *,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    newest: bool | Unset = False,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_after_seq: int | None | Unset
    if isinstance(after_seq, Unset):
        json_after_seq = UNSET
    else:
        json_after_seq = after_seq
    params["after_seq"] = json_after_seq

    json_before_seq: int | None | Unset
    if isinstance(before_seq, Unset):
        json_before_seq = UNSET
    else:
        json_before_seq = before_seq
    params["before_seq"] = json_before_seq

    params["newest"] = newest

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/thread".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
            message_id=quote(str(message_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ThreadResponse | None:
    if response.status_code == 200:
        response_200 = ThreadResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ThreadResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    newest: bool | Unset = False,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | ThreadResponse]:
    """List Thread

     A thread: its first line, and a page of its replies, oldest first
    within it -- from the start, after `after_seq`, before `before_seq`, or
    (`newest`) the latest.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        newest (bool | Unset): The newest page of replies, rather than the first. Default: False.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ThreadResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        after_seq=after_seq,
        before_seq=before_seq,
        newest=newest,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    newest: bool | Unset = False,
    limit: int | Unset = 50,
) -> ErrorResponse | ThreadResponse | None:
    """List Thread

     A thread: its first line, and a page of its replies, oldest first
    within it -- from the start, after `after_seq`, before `before_seq`, or
    (`newest`) the latest.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        newest (bool | Unset): The newest page of replies, rather than the first. Default: False.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ThreadResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        client=client,
        after_seq=after_seq,
        before_seq=before_seq,
        newest=newest,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    newest: bool | Unset = False,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | ThreadResponse]:
    """List Thread

     A thread: its first line, and a page of its replies, oldest first
    within it -- from the start, after `after_seq`, before `before_seq`, or
    (`newest`) the latest.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        newest (bool | Unset): The newest page of replies, rather than the first. Default: False.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ThreadResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        after_seq=after_seq,
        before_seq=before_seq,
        newest=newest,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after_seq: int | None | Unset = UNSET,
    before_seq: int | None | Unset = UNSET,
    newest: bool | Unset = False,
    limit: int | Unset = 50,
) -> ErrorResponse | ThreadResponse | None:
    """List Thread

     A thread: its first line, and a page of its replies, oldest first
    within it -- from the start, after `after_seq`, before `before_seq`, or
    (`newest`) the latest.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        after_seq (int | None | Unset):
        before_seq (int | None | Unset):
        newest (bool | Unset): The newest page of replies, rather than the first. Default: False.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ThreadResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            message_id=message_id,
            client=client,
            after_seq=after_seq,
            before_seq=before_seq,
            newest=newest,
            limit=limit,
        )
    ).parsed

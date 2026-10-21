import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_kind import ChannelKind
from ...models.error_response import ErrorResponse
from ...models.search_response import SearchResponse
from ...models.sender_kind import SenderKind
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    kind: ChannelKind | None | Unset = UNSET,
    sender_kind: None | SenderKind | Unset = UNSET,
    sender_id: None | Unset | UUID = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    archived: bool | Unset = False,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["q"] = q

    json_channel_id: None | str | Unset
    if isinstance(channel_id, Unset):
        json_channel_id = UNSET
    elif isinstance(channel_id, UUID):
        json_channel_id = str(channel_id)
    else:
        json_channel_id = channel_id
    params["channel_id"] = json_channel_id

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, ChannelKind):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    json_sender_kind: None | str | Unset
    if isinstance(sender_kind, Unset):
        json_sender_kind = UNSET
    elif isinstance(sender_kind, SenderKind):
        json_sender_kind = sender_kind.value
    else:
        json_sender_kind = sender_kind
    params["sender_kind"] = json_sender_kind

    json_sender_id: None | str | Unset
    if isinstance(sender_id, Unset):
        json_sender_id = UNSET
    elif isinstance(sender_id, UUID):
        json_sender_id = str(sender_id)
    else:
        json_sender_id = sender_id
    params["sender_id"] = json_sender_id

    json_since: None | str | Unset
    if isinstance(since, Unset):
        json_since = UNSET
    elif isinstance(since, datetime.datetime):
        json_since = since.isoformat()
    else:
        json_since = since
    params["since"] = json_since

    json_until: None | str | Unset
    if isinstance(until, Unset):
        json_until = UNSET
    elif isinstance(until, datetime.datetime):
        json_until = until.isoformat()
    else:
        json_until = until
    params["until"] = json_until

    params["archived"] = archived

    json_cursor: None | str | Unset
    if isinstance(cursor, Unset):
        json_cursor = UNSET
    else:
        json_cursor = cursor
    params["cursor"] = json_cursor

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/search".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SearchResponse | None:
    if response.status_code == 200:
        response_200 = SearchResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SearchResponse]:
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
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    kind: ChannelKind | None | Unset = UNSET,
    sender_kind: None | SenderKind | Unset = UNSET,
    sender_id: None | Unset | UUID = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    archived: bool | Unset = False,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> Response[ErrorResponse | SearchResponse]:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them -- each with its rank and the words around the
    match. An answer withheld from the reader is not found by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        kind (ChannelKind | None | Unset): Only channels of this kind.
        sender_kind (None | SenderKind | Unset): Only lines said by this person: with `sender_id`.
        sender_id (None | Unset | UUID): A member's user id, a contact's id or an agent's id.
        since (datetime.datetime | None | Unset): Said at or after.
        until (datetime.datetime | None | Unset): Said before.
        archived (bool | Unset): Search archived channels too. Default: False.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        q=q,
        channel_id=channel_id,
        kind=kind,
        sender_kind=sender_kind,
        sender_id=sender_id,
        since=since,
        until=until,
        archived=archived,
        cursor=cursor,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    kind: ChannelKind | None | Unset = UNSET,
    sender_kind: None | SenderKind | Unset = UNSET,
    sender_id: None | Unset | UUID = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    archived: bool | Unset = False,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> ErrorResponse | SearchResponse | None:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them -- each with its rank and the words around the
    match. An answer withheld from the reader is not found by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        kind (ChannelKind | None | Unset): Only channels of this kind.
        sender_kind (None | SenderKind | Unset): Only lines said by this person: with `sender_id`.
        sender_id (None | Unset | UUID): A member's user id, a contact's id or an agent's id.
        since (datetime.datetime | None | Unset): Said at or after.
        until (datetime.datetime | None | Unset): Said before.
        archived (bool | Unset): Search archived channels too. Default: False.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        q=q,
        channel_id=channel_id,
        kind=kind,
        sender_kind=sender_kind,
        sender_id=sender_id,
        since=since,
        until=until,
        archived=archived,
        cursor=cursor,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    kind: ChannelKind | None | Unset = UNSET,
    sender_kind: None | SenderKind | Unset = UNSET,
    sender_id: None | Unset | UUID = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    archived: bool | Unset = False,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> Response[ErrorResponse | SearchResponse]:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them -- each with its rank and the words around the
    match. An answer withheld from the reader is not found by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        kind (ChannelKind | None | Unset): Only channels of this kind.
        sender_kind (None | SenderKind | Unset): Only lines said by this person: with `sender_id`.
        sender_id (None | Unset | UUID): A member's user id, a contact's id or an agent's id.
        since (datetime.datetime | None | Unset): Said at or after.
        until (datetime.datetime | None | Unset): Said before.
        archived (bool | Unset): Search archived channels too. Default: False.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SearchResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        q=q,
        channel_id=channel_id,
        kind=kind,
        sender_kind=sender_kind,
        sender_id=sender_id,
        since=since,
        until=until,
        archived=archived,
        cursor=cursor,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    q: str,
    channel_id: None | Unset | UUID = UNSET,
    kind: ChannelKind | None | Unset = UNSET,
    sender_kind: None | SenderKind | Unset = UNSET,
    sender_id: None | Unset | UUID = UNSET,
    since: datetime.datetime | None | Unset = UNSET,
    until: datetime.datetime | None | Unset = UNSET,
    archived: bool | Unset = False,
    cursor: None | str | Unset = UNSET,
    limit: int | Unset = 30,
) -> ErrorResponse | SearchResponse | None:
    """Search

     Lines matching every word, newest first, in the channels the reader
    sees -- or in one of them -- each with its rank and the words around the
    match. An answer withheld from the reader is not found by what it said.

    Args:
        pod_id (UUID):
        q (str):
        channel_id (None | Unset | UUID):
        kind (ChannelKind | None | Unset): Only channels of this kind.
        sender_kind (None | SenderKind | Unset): Only lines said by this person: with `sender_id`.
        sender_id (None | Unset | UUID): A member's user id, a contact's id or an agent's id.
        since (datetime.datetime | None | Unset): Said at or after.
        until (datetime.datetime | None | Unset): Said before.
        archived (bool | Unset): Search archived channels too. Default: False.
        cursor (None | str | Unset):
        limit (int | Unset):  Default: 30.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SearchResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            q=q,
            channel_id=channel_id,
            kind=kind,
            sender_kind=sender_kind,
            sender_id=sender_id,
            since=since,
            until=until,
            archived=archived,
            cursor=cursor,
            limit=limit,
        )
    ).parsed

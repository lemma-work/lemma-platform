from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_kind import ChannelKind
from ...models.channel_list_response import ChannelListResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    kind: ChannelKind | None | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_kind: None | str | Unset
    if isinstance(kind, Unset):
        json_kind = UNSET
    elif isinstance(kind, ChannelKind):
        json_kind = kind.value
    else:
        json_kind = kind
    params["kind"] = json_kind

    params["limit"] = limit

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    else:
        json_before = before
    params["before"] = json_before

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChannelListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ChannelListResponse.from_dict(response.json())

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
) -> Response[ChannelListResponse | ErrorResponse]:
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
    kind: ChannelKind | None | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> Response[ChannelListResponse | ErrorResponse]:
    """List Channels

     The pod's channels this member sees, most recently active first.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        kind=kind,
        limit=limit,
        before=before,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> ChannelListResponse | ErrorResponse | None:
    """List Channels

     The pod's channels this member sees, most recently active first.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        kind=kind,
        limit=limit,
        before=before,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> Response[ChannelListResponse | ErrorResponse]:
    """List Channels

     The pod's channels this member sees, most recently active first.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        kind=kind,
        limit=limit,
        before=before,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    kind: ChannelKind | None | Unset = UNSET,
    limit: int | Unset = 50,
    before: None | str | Unset = UNSET,
) -> ChannelListResponse | ErrorResponse | None:
    """List Channels

     The pod's channels this member sees, most recently active first.

    Args:
        pod_id (UUID):
        kind (ChannelKind | None | Unset):
        limit (int | Unset):  Default: 50.
        before (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            kind=kind,
            limit=limit,
            before=before,
        )
    ).parsed

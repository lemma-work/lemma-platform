from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    path: str,
    offset: int | Unset = 0,
    length: int | None | Unset = UNSET,
    range_: None | str | Unset = UNSET,
    if_none_match: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(range_, Unset):
        headers["Range"] = range_

    if not isinstance(if_none_match, Unset):
        headers["If-None-Match"] = if_none_match

    params: dict[str, Any] = {}

    params["path"] = path

    params["offset"] = offset

    json_length: int | None | Unset
    if isinstance(length, Unset):
        json_length = UNSET
    else:
        json_length = length
    params["length"] = json_length

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/workspace/files:content",
        "params": params,
    }

    _kwargs["headers"] = headers
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
    *,
    client: AuthenticatedClient | Client,
    path: str,
    offset: int | Unset = 0,
    length: int | None | Unset = UNSET,
    range_: None | str | Unset = UNSET,
    if_none_match: None | str | Unset = UNSET,
) -> Response[Any | ErrorResponse]:
    """Read workspace file content

    Args:
        path (str):
        offset (int | Unset):  Default: 0.
        length (int | None | Unset):
        range_ (None | str | Unset):
        if_none_match (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        path=path,
        offset=offset,
        length=length,
        range_=range_,
        if_none_match=if_none_match,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    offset: int | Unset = 0,
    length: int | None | Unset = UNSET,
    range_: None | str | Unset = UNSET,
    if_none_match: None | str | Unset = UNSET,
) -> Any | ErrorResponse | None:
    """Read workspace file content

    Args:
        path (str):
        offset (int | Unset):  Default: 0.
        length (int | None | Unset):
        range_ (None | str | Unset):
        if_none_match (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        client=client,
        path=path,
        offset=offset,
        length=length,
        range_=range_,
        if_none_match=if_none_match,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    offset: int | Unset = 0,
    length: int | None | Unset = UNSET,
    range_: None | str | Unset = UNSET,
    if_none_match: None | str | Unset = UNSET,
) -> Response[Any | ErrorResponse]:
    """Read workspace file content

    Args:
        path (str):
        offset (int | Unset):  Default: 0.
        length (int | None | Unset):
        range_ (None | str | Unset):
        if_none_match (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        path=path,
        offset=offset,
        length=length,
        range_=range_,
        if_none_match=if_none_match,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    path: str,
    offset: int | Unset = 0,
    length: int | None | Unset = UNSET,
    range_: None | str | Unset = UNSET,
    if_none_match: None | str | Unset = UNSET,
) -> Any | ErrorResponse | None:
    """Read workspace file content

    Args:
        path (str):
        offset (int | Unset):  Default: 0.
        length (int | None | Unset):
        range_ (None | str | Unset):
        if_none_match (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            path=path,
            offset=offset,
            length=length,
            range_=range_,
            if_none_match=if_none_match,
        )
    ).parsed

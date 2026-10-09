from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.rows_response import RowsResponse
from ...types import UNSET, Response


def _get_kwargs(
    public_key: str,
    *,
    table: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["table"] = table

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/public/web/{public_key}/rows".format(
            public_key=quote(str(public_key), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | RowsResponse | None:
    if response.status_code == 200:
        response_200 = RowsResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | RowsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
) -> Response[ErrorResponse | RowsResponse]:
    """Web Read Rows

     Read a table the pod opened for reads: its open columns, every row.

    The read side of a form. A booking page reads its free slots here; nothing
    else about the pod is reachable, and a table that takes rows from outside
    is never readable.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RowsResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        table=table,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
) -> ErrorResponse | RowsResponse | None:
    """Web Read Rows

     Read a table the pod opened for reads: its open columns, every row.

    The read side of a form. A booking page reads its free slots here; nothing
    else about the pod is reachable, and a table that takes rows from outside
    is never readable.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RowsResponse
    """

    return sync_detailed(
        public_key=public_key,
        client=client,
        table=table,
    ).parsed


async def asyncio_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
) -> Response[ErrorResponse | RowsResponse]:
    """Web Read Rows

     Read a table the pod opened for reads: its open columns, every row.

    The read side of a form. A booking page reads its free slots here; nothing
    else about the pod is reachable, and a table that takes rows from outside
    is never readable.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RowsResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        table=table,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
) -> ErrorResponse | RowsResponse | None:
    """Web Read Rows

     Read a table the pod opened for reads: its open columns, every row.

    The read side of a form. A booking page reads its free slots here; nothing
    else about the pod is reachable, and a table that takes rows from outside
    is never readable.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RowsResponse
    """

    return (
        await asyncio_detailed(
            public_key=public_key,
            client=client,
            table=table,
        )
    ).parsed

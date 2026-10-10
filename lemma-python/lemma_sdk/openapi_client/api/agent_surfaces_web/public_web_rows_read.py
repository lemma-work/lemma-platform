from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.public_rows_response import PublicRowsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    public_key: str,
    *,
    table: str,
    order_by: None | str | Unset = UNSET,
    desc: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["table"] = table

    json_order_by: None | str | Unset
    if isinstance(order_by, Unset):
        json_order_by = UNSET
    else:
        json_order_by = order_by
    params["order_by"] = json_order_by

    params["desc"] = desc

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
) -> ErrorResponse | PublicRowsResponse | None:
    if response.status_code == 200:
        response_200 = PublicRowsResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PublicRowsResponse]:
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
    order_by: None | str | Unset = UNSET,
    desc: bool | Unset = False,
) -> Response[ErrorResponse | PublicRowsResponse]:
    """Web Read Rows

     Read a table the pod marked Public: every row, at most 500.

    The same reading the pod's chat does for this visitor -- Public, and
    nothing else. A booking page reads its free slots here.

    Args:
        public_key (str):
        table (str):
        order_by (None | str | Unset):
        desc (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PublicRowsResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        table=table,
        order_by=order_by,
        desc=desc,
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
    order_by: None | str | Unset = UNSET,
    desc: bool | Unset = False,
) -> ErrorResponse | PublicRowsResponse | None:
    """Web Read Rows

     Read a table the pod marked Public: every row, at most 500.

    The same reading the pod's chat does for this visitor -- Public, and
    nothing else. A booking page reads its free slots here.

    Args:
        public_key (str):
        table (str):
        order_by (None | str | Unset):
        desc (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PublicRowsResponse
    """

    return sync_detailed(
        public_key=public_key,
        client=client,
        table=table,
        order_by=order_by,
        desc=desc,
    ).parsed


async def asyncio_detailed(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
    order_by: None | str | Unset = UNSET,
    desc: bool | Unset = False,
) -> Response[ErrorResponse | PublicRowsResponse]:
    """Web Read Rows

     Read a table the pod marked Public: every row, at most 500.

    The same reading the pod's chat does for this visitor -- Public, and
    nothing else. A booking page reads its free slots here.

    Args:
        public_key (str):
        table (str):
        order_by (None | str | Unset):
        desc (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PublicRowsResponse]
    """

    kwargs = _get_kwargs(
        public_key=public_key,
        table=table,
        order_by=order_by,
        desc=desc,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    public_key: str,
    *,
    client: AuthenticatedClient | Client,
    table: str,
    order_by: None | str | Unset = UNSET,
    desc: bool | Unset = False,
) -> ErrorResponse | PublicRowsResponse | None:
    """Web Read Rows

     Read a table the pod marked Public: every row, at most 500.

    The same reading the pod's chat does for this visitor -- Public, and
    nothing else. A booking page reads its free slots here.

    Args:
        public_key (str):
        table (str):
        order_by (None | str | Unset):
        desc (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PublicRowsResponse
    """

    return (
        await asyncio_detailed(
            public_key=public_key,
            client=client,
            table=table,
            order_by=order_by,
            desc=desc,
        )
    ).parsed

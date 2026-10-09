from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.table_response import TableResponse
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
        "url": "/public/web/{public_key}/table".format(
            public_key=quote(str(public_key), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TableResponse | None:
    if response.status_code == 200:
        response_200 = TableResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | TableResponse]:
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
) -> Response[ErrorResponse | TableResponse]:
    """Web Read Table

     What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableResponse]
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
) -> ErrorResponse | TableResponse | None:
    """Web Read Table

     What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableResponse
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
) -> Response[ErrorResponse | TableResponse]:
    """Web Read Table

     What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableResponse]
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
) -> ErrorResponse | TableResponse | None:
    """Web Read Table

     What a page may ask for on a table the pod opened to visitors.

    The open columns only, in order: enough to draw a form, and nothing else
    about the table or its rows.

    Args:
        public_key (str):
        table (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableResponse
    """

    return (
        await asyncio_detailed(
            public_key=public_key,
            client=client,
            table=table,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.web_login_list_response import WebLoginListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ref: str,
    *,
    wake: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["wake"] = wake

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/machines/{ref}/browser/logins".format(
            ref=quote(str(ref), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WebLoginListResponse | None:
    if response.status_code == 200:
        response_200 = WebLoginListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | WebLoginListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WebLoginListResponse]:
    """The sites a machine's browser is signed in to

    Args:
        ref (str):
        wake (bool | Unset): Start the machine if it is paused. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebLoginListResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        wake=wake,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> ErrorResponse | WebLoginListResponse | None:
    """The sites a machine's browser is signed in to

    Args:
        ref (str):
        wake (bool | Unset): Start the machine if it is paused. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebLoginListResponse
    """

    return sync_detailed(
        ref=ref,
        client=client,
        wake=wake,
    ).parsed


async def asyncio_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WebLoginListResponse]:
    """The sites a machine's browser is signed in to

    Args:
        ref (str):
        wake (bool | Unset): Start the machine if it is paused. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebLoginListResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        wake=wake,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> ErrorResponse | WebLoginListResponse | None:
    """The sites a machine's browser is signed in to

    Args:
        ref (str):
        wake (bool | Unset): Start the machine if it is paused. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebLoginListResponse
    """

    return (
        await asyncio_detailed(
            ref=ref,
            client=client,
            wake=wake,
        )
    ).parsed

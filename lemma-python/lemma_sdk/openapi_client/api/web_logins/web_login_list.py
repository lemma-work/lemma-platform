from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.web_login_list_response import WebLoginListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    wake: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["wake"] = wake

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/web-logins",
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
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WebLoginListResponse]:
    """List the sites your browser is signed in to

    Args:
        wake (bool | Unset): Start the computer if it is paused. Off by default so that rendering
            this list is never what wakes one. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebLoginListResponse]
    """

    kwargs = _get_kwargs(
        wake=wake,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> ErrorResponse | WebLoginListResponse | None:
    """List the sites your browser is signed in to

    Args:
        wake (bool | Unset): Start the computer if it is paused. Off by default so that rendering
            this list is never what wakes one. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebLoginListResponse
    """

    return sync_detailed(
        client=client,
        wake=wake,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WebLoginListResponse]:
    """List the sites your browser is signed in to

    Args:
        wake (bool | Unset): Start the computer if it is paused. Off by default so that rendering
            this list is never what wakes one. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WebLoginListResponse]
    """

    kwargs = _get_kwargs(
        wake=wake,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    wake: bool | Unset = False,
) -> ErrorResponse | WebLoginListResponse | None:
    """List the sites your browser is signed in to

    Args:
        wake (bool | Unset): Start the computer if it is paused. Off by default so that rendering
            this list is never what wakes one. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WebLoginListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            wake=wake,
        )
    ).parsed

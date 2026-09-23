from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.forget_response import ForgetResponse
from ...types import UNSET, Response


def _get_kwargs(
    *,
    origin: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["origin"] = origin

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/web-logins",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ForgetResponse | None:
    if response.status_code == 200:
        response_200 = ForgetResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ForgetResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        origin=origin,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
) -> ErrorResponse | ForgetResponse | None:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return sync_detailed(
        client=client,
        origin=origin,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        origin=origin,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    origin: str,
) -> ErrorResponse | ForgetResponse | None:
    """Sign your browser out of a site

    Args:
        origin (str): The site to forget, as an origin or a host.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            origin=origin,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.forget_response import ForgetResponse
from ...types import Response


def _get_kwargs(
    ref: str,
    site: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/machines/{ref}/browser/logins/{site}".format(
            ref=quote(str(ref), safe=""),
            site=quote(str(site), safe=""),
        ),
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
    ref: str,
    site: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign a machine's browser out of a site

     Explicit and per site: the only way a login is removed from a machine
    short of a confirmed `home` reset.

    Args:
        ref (str):
        site (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        site=site,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    site: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ForgetResponse | None:
    """Sign a machine's browser out of a site

     Explicit and per site: the only way a login is removed from a machine
    short of a confirmed `home` reset.

    Args:
        ref (str):
        site (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return sync_detailed(
        ref=ref,
        site=site,
        client=client,
    ).parsed


async def asyncio_detailed(
    ref: str,
    site: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ForgetResponse]:
    """Sign a machine's browser out of a site

     Explicit and per site: the only way a login is removed from a machine
    short of a confirmed `home` reset.

    Args:
        ref (str):
        site (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ForgetResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        site=site,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    site: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ForgetResponse | None:
    """Sign a machine's browser out of a site

     Explicit and per site: the only way a login is removed from a machine
    short of a confirmed `home` reset.

    Args:
        ref (str):
        site (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ForgetResponse
    """

    return (
        await asyncio_detailed(
            ref=ref,
            site=site,
            client=client,
        )
    ).parsed

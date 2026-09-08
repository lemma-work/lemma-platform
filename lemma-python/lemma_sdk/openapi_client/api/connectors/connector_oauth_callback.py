from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_error: None | str | Unset
    if isinstance(error, Unset):
        json_error = UNSET
    else:
        json_error = error
    params["error"] = json_error

    json_format_: None | str | Unset
    if isinstance(format_, Unset):
        json_format_ = UNSET
    else:
        json_format_ = format_
    params["format"] = json_format_

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/connectors/connect-requests/oauth/callback",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | str | None:
    if response.status_code == 200:
        response_200 = response.text
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
) -> Response[ErrorResponse | str]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> Response[ErrorResponse | str]:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security. It redirects back into the app unless JSON is explicitly requested.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | str]
    """

    kwargs = _get_kwargs(
        error=error,
        format_=format_,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> ErrorResponse | str | None:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security. It redirects back into the app unless JSON is explicitly requested.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | str
    """

    return sync_detailed(
        client=client,
        error=error,
        format_=format_,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> Response[ErrorResponse | str]:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security. It redirects back into the app unless JSON is explicitly requested.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | str]
    """

    kwargs = _get_kwargs(
        error=error,
        format_=format_,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> ErrorResponse | str | None:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security. It redirects back into the app unless JSON is explicitly requested.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | str
    """

    return (
        await asyncio_detailed(
            client=client,
            error=error,
            format_=format_,
        )
    ).parsed

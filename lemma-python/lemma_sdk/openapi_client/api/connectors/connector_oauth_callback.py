from http import HTTPStatus
from typing import Any, cast

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
) -> Any | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = cast(Any, None)
        return response_200

    if response.status_code == 303:
        response_303 = cast(Any, None)
        return response_303

    if response.status_code == 307:
        response_307 = cast(Any, None)
        return response_307

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

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
    error: None | str | Unset = UNSET,
    format_: None | str | Unset = UNSET,
) -> Response[Any | ErrorResponse]:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security.

    A browser is redirected back into the app (303) carrying the outcome as query parameters: `connect`
    is one of `connected`, `install_required`, `pending_approval`, `install_received` or `error`. Pass
    `format=json` (or an `Accept` header of `application/json` without `text/html`) to receive the
    account as JSON instead.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset): Set to `json` to receive the account instead of a redirect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
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
) -> Any | ErrorResponse | None:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security.

    A browser is redirected back into the app (303) carrying the outcome as query parameters: `connect`
    is one of `connected`, `install_required`, `pending_approval`, `install_received` or `error`. Pass
    `format=json` (or an `Accept` header of `application/json` without `text/html`) to receive the
    account as JSON instead.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset): Set to `json` to receive the account instead of a redirect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
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
) -> Response[Any | ErrorResponse]:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security.

    A browser is redirected back into the app (303) carrying the outcome as query parameters: `connect`
    is one of `connected`, `install_required`, `pending_approval`, `install_received` or `error`. Pass
    `format=json` (or an `Accept` header of `application/json` without `text/html`) to receive the
    account as JSON instead.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset): Set to `json` to receive the account instead of a redirect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
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
) -> Any | ErrorResponse | None:
    """OAuth Callback

     Handle OAuth callback and complete account connection. This endpoint is public and uses the state
    parameter for security.

    A browser is redirected back into the app (303) carrying the outcome as query parameters: `connect`
    is one of `connected`, `install_required`, `pending_approval`, `install_received` or `error`. Pass
    `format=json` (or an `Accept` header of `application/json` without `text/html`) to receive the
    account as JSON instead.

    Args:
        error (None | str | Unset):
        format_ (None | str | Unset): Set to `json` to receive the account instead of a redirect.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            error=error,
            format_=format_,
        )
    ).parsed

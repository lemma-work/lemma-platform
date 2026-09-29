from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.telegram_link_request import TelegramLinkRequest
from ...models.telegram_link_response import TelegramLinkResponse
from ...types import Response


def _get_kwargs(
    *,
    body: TelegramLinkRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/surfaces/me/telegram-link",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TelegramLinkResponse | None:
    if response.status_code == 200:
        response_200 = TelegramLinkResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | TelegramLinkResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: TelegramLinkRequest,
) -> Response[ErrorResponse | TelegramLinkResponse]:
    """Create My Telegram Link

     Mint a one-time ``t.me`` link that connects the Telegram chat opening it
    to the current user, answered by ``pod_id``'s agent (or the suggested pod
    when omitted). Expires after ten minutes and works once. 403 for a pod the
    user cannot attach a chat to; 409 when there is no shared Telegram bot.

    Args:
        body (TelegramLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TelegramLinkResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: TelegramLinkRequest,
) -> ErrorResponse | TelegramLinkResponse | None:
    """Create My Telegram Link

     Mint a one-time ``t.me`` link that connects the Telegram chat opening it
    to the current user, answered by ``pod_id``'s agent (or the suggested pod
    when omitted). Expires after ten minutes and works once. 403 for a pod the
    user cannot attach a chat to; 409 when there is no shared Telegram bot.

    Args:
        body (TelegramLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TelegramLinkResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: TelegramLinkRequest,
) -> Response[ErrorResponse | TelegramLinkResponse]:
    """Create My Telegram Link

     Mint a one-time ``t.me`` link that connects the Telegram chat opening it
    to the current user, answered by ``pod_id``'s agent (or the suggested pod
    when omitted). Expires after ten minutes and works once. 403 for a pod the
    user cannot attach a chat to; 409 when there is no shared Telegram bot.

    Args:
        body (TelegramLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TelegramLinkResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: TelegramLinkRequest,
) -> ErrorResponse | TelegramLinkResponse | None:
    """Create My Telegram Link

     Mint a one-time ``t.me`` link that connects the Telegram chat opening it
    to the current user, answered by ``pod_id``'s agent (or the suggested pod
    when omitted). Expires after ten minutes and works once. 403 for a pod the
    user cannot attach a chat to; 409 when there is no shared Telegram bot.

    Args:
        body (TelegramLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TelegramLinkResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed

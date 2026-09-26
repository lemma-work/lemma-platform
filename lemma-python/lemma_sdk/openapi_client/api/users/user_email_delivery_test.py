from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.email_delivery_test_response import EmailDeliveryTestResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/users/me/email-delivery/test",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EmailDeliveryTestResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = EmailDeliveryTestResponse.from_dict(response.json())

        return response_200

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[EmailDeliveryTestResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[EmailDeliveryTestResponse | ErrorResponse]:
    """Send A Test Email

     Send a short test email to the signed-in user's own address with this installation's email settings.
    Local installations only; 404 elsewhere.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmailDeliveryTestResponse | ErrorResponse]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> EmailDeliveryTestResponse | ErrorResponse | None:
    """Send A Test Email

     Send a short test email to the signed-in user's own address with this installation's email settings.
    Local installations only; 404 elsewhere.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmailDeliveryTestResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[EmailDeliveryTestResponse | ErrorResponse]:
    """Send A Test Email

     Send a short test email to the signed-in user's own address with this installation's email settings.
    Local installations only; 404 elsewhere.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmailDeliveryTestResponse | ErrorResponse]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> EmailDeliveryTestResponse | ErrorResponse | None:
    """Send A Test Email

     Send a short test email to the signed-in user's own address with this installation's email settings.
    Local installations only; 404 elsewhere.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmailDeliveryTestResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed

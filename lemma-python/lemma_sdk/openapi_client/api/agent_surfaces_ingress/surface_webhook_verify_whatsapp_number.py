from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    phone_number_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/surfaces/webhooks/whatsapp/numbers/{phone_number_id}".format(
            phone_number_id=quote(str(phone_number_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = response.json()
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
) -> Response[Any | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    phone_number_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Verify a pooled WhatsApp number's own callback URL

     Webhook verification endpoint for one pooled WhatsApp number.

    Args:
        phone_number_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        phone_number_id=phone_number_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    phone_number_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Verify a pooled WhatsApp number's own callback URL

     Webhook verification endpoint for one pooled WhatsApp number.

    Args:
        phone_number_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        phone_number_id=phone_number_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    phone_number_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | ErrorResponse]:
    """Verify a pooled WhatsApp number's own callback URL

     Webhook verification endpoint for one pooled WhatsApp number.

    Args:
        phone_number_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        phone_number_id=phone_number_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    phone_number_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | ErrorResponse | None:
    """Verify a pooled WhatsApp number's own callback URL

     Webhook verification endpoint for one pooled WhatsApp number.

    Args:
        phone_number_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            phone_number_id=phone_number_id,
            client=client,
        )
    ).parsed

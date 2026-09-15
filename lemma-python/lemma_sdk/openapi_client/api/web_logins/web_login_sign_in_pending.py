from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pending_sign_in_response import PendingSignInResponse
from ...types import Response


def _get_kwargs(
    conversation_id: UUID,
    tool_call_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/web-logins/sign-ins/{conversation_id}/{tool_call_id}".format(
            conversation_id=quote(str(conversation_id), safe=""),
            tool_call_id=quote(str(tool_call_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PendingSignInResponse | None:
    if response.status_code == 200:
        response_200 = PendingSignInResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PendingSignInResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PendingSignInResponse]:
    """What a sign-in link is asking for

    Args:
        conversation_id (UUID):
        tool_call_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PendingSignInResponse]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PendingSignInResponse | None:
    """What a sign-in link is asking for

    Args:
        conversation_id (UUID):
        tool_call_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PendingSignInResponse
    """

    return sync_detailed(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PendingSignInResponse]:
    """What a sign-in link is asking for

    Args:
        conversation_id (UUID):
        tool_call_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PendingSignInResponse]
    """

    kwargs = _get_kwargs(
        conversation_id=conversation_id,
        tool_call_id=tool_call_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    conversation_id: UUID,
    tool_call_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PendingSignInResponse | None:
    """What a sign-in link is asking for

    Args:
        conversation_id (UUID):
        tool_call_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PendingSignInResponse
    """

    return (
        await asyncio_detailed(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            client=client,
        )
    ).parsed

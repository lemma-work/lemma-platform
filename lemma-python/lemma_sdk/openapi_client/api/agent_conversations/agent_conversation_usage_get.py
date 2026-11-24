from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.conversation_usage_response import ConversationUsageResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/usage".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConversationUsageResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConversationUsageResponse.from_dict(response.json())

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
) -> Response[ConversationUsageResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ConversationUsageResponse | ErrorResponse]:
    """Get Conversation Usage

     What a conversation has cost so far, in US dollars and model tokens: summed over every run, and run
    by run for the newest 200. Readable by whoever may read the conversation (its owner), and only from
    what was recorded against them: the same rows their own usage shows. A run with nothing recorded has
    null `usage` and is counted in `unreported_run_count`: an Agent Host run whose harness reports no
    usage, or one not reported yet. Sub-agent conversations are not included; read each one's own.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationUsageResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ConversationUsageResponse | ErrorResponse | None:
    """Get Conversation Usage

     What a conversation has cost so far, in US dollars and model tokens: summed over every run, and run
    by run for the newest 200. Readable by whoever may read the conversation (its owner), and only from
    what was recorded against them: the same rows their own usage shows. A run with nothing recorded has
    null `usage` and is counted in `unreported_run_count`: an Agent Host run whose harness reports no
    usage, or one not reported yet. Sub-agent conversations are not included; read each one's own.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationUsageResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        conversation_id=conversation_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ConversationUsageResponse | ErrorResponse]:
    """Get Conversation Usage

     What a conversation has cost so far, in US dollars and model tokens: summed over every run, and run
    by run for the newest 200. Readable by whoever may read the conversation (its owner), and only from
    what was recorded against them: the same rows their own usage shows. A run with nothing recorded has
    null `usage` and is counted in `unreported_run_count`: an Agent Host run whose harness reports no
    usage, or one not reported yet. Sub-agent conversations are not included; read each one's own.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationUsageResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ConversationUsageResponse | ErrorResponse | None:
    """Get Conversation Usage

     What a conversation has cost so far, in US dollars and model tokens: summed over every run, and run
    by run for the newest 200. Readable by whoever may read the conversation (its owner), and only from
    what was recorded against them: the same rows their own usage shows. A run with nothing recorded has
    null `usage` and is counted in `unreported_run_count`: an Agent Host run whose harness reports no
    usage, or one not reported yet. Sub-agent conversations are not included; read each one's own.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationUsageResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            client=client,
        )
    ).parsed

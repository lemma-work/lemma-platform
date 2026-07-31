from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_run_start_response import AgentRunStartResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/retry".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentRunStartResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentRunStartResponse.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[AgentRunStartResponse | ErrorResponse]:
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
) -> Response[AgentRunStartResponse | ErrorResponse]:
    """Retry Failed Pod Conversation Run

     Start a new run from the latest failed run's persisted conversation history without appending a
    duplicate user message. Retry is allowed only when the failed run produced no assistant, tool, or
    system activity. Attach to the returned run with the conversation stream endpoint.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunStartResponse | ErrorResponse]
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
) -> AgentRunStartResponse | ErrorResponse | None:
    """Retry Failed Pod Conversation Run

     Start a new run from the latest failed run's persisted conversation history without appending a
    duplicate user message. Retry is allowed only when the failed run produced no assistant, tool, or
    system activity. Attach to the returned run with the conversation stream endpoint.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRunStartResponse | ErrorResponse
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
) -> Response[AgentRunStartResponse | ErrorResponse]:
    """Retry Failed Pod Conversation Run

     Start a new run from the latest failed run's persisted conversation history without appending a
    duplicate user message. Retry is allowed only when the failed run produced no assistant, tool, or
    system activity. Attach to the returned run with the conversation stream endpoint.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunStartResponse | ErrorResponse]
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
) -> AgentRunStartResponse | ErrorResponse | None:
    """Retry Failed Pod Conversation Run

     Start a new run from the latest failed run's persisted conversation history without appending a
    duplicate user message. Retry is allowed only when the failed run produced no assistant, tool, or
    system activity. Attach to the returned run with the conversation stream endpoint.

    Args:
        pod_id (UUID):
        conversation_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRunStartResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            client=client,
        )
    ).parsed

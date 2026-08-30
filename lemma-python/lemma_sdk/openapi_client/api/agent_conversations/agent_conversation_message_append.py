from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_run_start_response import AgentRunStartResponse
from ...models.error_response import ErrorResponse
from ...models.send_message_request import SendMessageRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    body: SendMessageRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/messages/append".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
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
    body: SendMessageRequest,
) -> Response[AgentRunStartResponse | ErrorResponse]:
    """Append Pod Conversation Message

     Append a user message without opening a Server-Sent Events stream. When a run is already active for
    the conversation, the message joins that run and the next harness step sees it in persisted history
    -- any stream already subscribed to the conversation surfaces the resulting events, so callers
    steering an in-flight run should attach to that stream rather than opening a second one here. When
    no run is active, this starts a new one exactly like the streaming send route, just without
    attaching a stream to it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (SendMessageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunStartResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        body=body,
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
    body: SendMessageRequest,
) -> AgentRunStartResponse | ErrorResponse | None:
    """Append Pod Conversation Message

     Append a user message without opening a Server-Sent Events stream. When a run is already active for
    the conversation, the message joins that run and the next harness step sees it in persisted history
    -- any stream already subscribed to the conversation surfaces the resulting events, so callers
    steering an in-flight run should attach to that stream rather than opening a second one here. When
    no run is active, this starts a new one exactly like the streaming send route, just without
    attaching a stream to it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (SendMessageRequest):

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
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SendMessageRequest,
) -> Response[AgentRunStartResponse | ErrorResponse]:
    """Append Pod Conversation Message

     Append a user message without opening a Server-Sent Events stream. When a run is already active for
    the conversation, the message joins that run and the next harness step sees it in persisted history
    -- any stream already subscribed to the conversation surfaces the resulting events, so callers
    steering an in-flight run should attach to that stream rather than opening a second one here. When
    no run is active, this starts a new one exactly like the streaming send route, just without
    attaching a stream to it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (SendMessageRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunStartResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SendMessageRequest,
) -> AgentRunStartResponse | ErrorResponse | None:
    """Append Pod Conversation Message

     Append a user message without opening a Server-Sent Events stream. When a run is already active for
    the conversation, the message joins that run and the next harness step sees it in persisted history
    -- any stream already subscribed to the conversation surfaces the resulting events, so callers
    steering an in-flight run should attach to that stream rather than opening a second one here. When
    no run is active, this starts a new one exactly like the streaming send route, just without
    attaching a stream to it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (SendMessageRequest):

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
            body=body,
        )
    ).parsed

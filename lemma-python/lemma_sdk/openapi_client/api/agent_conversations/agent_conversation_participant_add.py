from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.add_conversation_participant_request import (
    AddConversationParticipantRequest,
)
from ...models.conversation_participant_response import ConversationParticipantResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    body: AddConversationParticipantRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/participants".format(
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
) -> ConversationParticipantResponse | ErrorResponse | None:
    if response.status_code == 201:
        response_201 = ConversationParticipantResponse.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ConversationParticipantResponse | ErrorResponse]:
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
    body: AddConversationParticipantRequest,
) -> Response[ConversationParticipantResponse | ErrorResponse]:
    """Add Conversation Participant

     Add one person, or one agent, to a conversation. Name exactly one of user_id or agent_name. Adding a
    person is a grant: every answer in the conversation is from then on said to them. Their own working
    stays private to them, and so does everyone else's.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (AddConversationParticipantRequest): Add one person or one agent. Naming both, or
            neither, is rejected.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationParticipantResponse | ErrorResponse]
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
    body: AddConversationParticipantRequest,
) -> ConversationParticipantResponse | ErrorResponse | None:
    """Add Conversation Participant

     Add one person, or one agent, to a conversation. Name exactly one of user_id or agent_name. Adding a
    person is a grant: every answer in the conversation is from then on said to them. Their own working
    stays private to them, and so does everyone else's.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (AddConversationParticipantRequest): Add one person or one agent. Naming both, or
            neither, is rejected.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationParticipantResponse | ErrorResponse
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
    body: AddConversationParticipantRequest,
) -> Response[ConversationParticipantResponse | ErrorResponse]:
    """Add Conversation Participant

     Add one person, or one agent, to a conversation. Name exactly one of user_id or agent_name. Adding a
    person is a grant: every answer in the conversation is from then on said to them. Their own working
    stays private to them, and so does everyone else's.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (AddConversationParticipantRequest): Add one person or one agent. Naming both, or
            neither, is rejected.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationParticipantResponse | ErrorResponse]
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
    body: AddConversationParticipantRequest,
) -> ConversationParticipantResponse | ErrorResponse | None:
    """Add Conversation Participant

     Add one person, or one agent, to a conversation. Name exactly one of user_id or agent_name. Adding a
    person is a grant: every answer in the conversation is from then on said to them. Their own working
    stays private to them, and so does everyone else's.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        body (AddConversationParticipantRequest): Add one person or one agent. Naming both, or
            neither, is rejected.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationParticipantResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            client=client,
            body=body,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.conversation_response import ConversationResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    agent_name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_agent_name: None | str | Unset
    if isinstance(agent_name, Unset):
        json_agent_name = UNSET
    else:
        json_agent_name = agent_name
    params["agent_name"] = json_agent_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/conversations/open".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConversationResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConversationResponse.from_dict(response.json())

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
) -> Response[ConversationResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> Response[ConversationResponse | ErrorResponse]:
    """Open Pod Agent Conversation

     Return the caller's ongoing conversation with an agent, opening one if there is none yet. Omit
    agent_name for the default pod assistant. Unlike create, calling this twice returns the same
    conversation: it is where a person lands when they open the agent rather than a new session each
    time. Archived, task, project and surface-bound conversations are never returned.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        agent_name=agent_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> ConversationResponse | ErrorResponse | None:
    """Open Pod Agent Conversation

     Return the caller's ongoing conversation with an agent, opening one if there is none yet. Omit
    agent_name for the default pod assistant. Unlike create, calling this twice returns the same
    conversation: it is where a person lands when they open the agent rather than a new session each
    time. Archived, task, project and surface-bound conversations are never returned.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        agent_name=agent_name,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> Response[ConversationResponse | ErrorResponse]:
    """Open Pod Agent Conversation

     Return the caller's ongoing conversation with an agent, opening one if there is none yet. Omit
    agent_name for the default pod assistant. Unlike create, calling this twice returns the same
    conversation: it is where a person lands when they open the agent rather than a new session each
    time. Archived, task, project and surface-bound conversations are never returned.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        agent_name=agent_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> ConversationResponse | ErrorResponse | None:
    """Open Pod Agent Conversation

     Return the caller's ongoing conversation with an agent, opening one if there is none yet. Omit
    agent_name for the default pod assistant. Unlike create, calling this twice returns the same
    conversation: it is where a person lands when they open the agent rather than a new session each
    time. Archived, task, project and surface-bound conversations are never returned.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            agent_name=agent_name,
        )
    ).parsed

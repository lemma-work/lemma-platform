from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_user_id: None | str | Unset
    if isinstance(user_id, Unset):
        json_user_id = UNSET
    elif isinstance(user_id, UUID):
        json_user_id = str(user_id)
    else:
        json_user_id = user_id
    params["user_id"] = json_user_id

    json_agent_id: None | str | Unset
    if isinstance(agent_id, Unset):
        json_agent_id = UNSET
    elif isinstance(agent_id, UUID):
        json_agent_id = str(agent_id)
    else:
        json_agent_id = agent_id
    params["agent_id"] = json_agent_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/participants".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

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
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
) -> Response[Any | ErrorResponse]:
    """Remove Conversation Participant

     Remove one person, or one agent. Name exactly one of user_id or agent_id. The person who opened the
    conversation cannot be removed from it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
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
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
) -> Any | ErrorResponse | None:
    """Remove Conversation Participant

     Remove one person, or one agent. Name exactly one of user_id or agent_id. The person who opened the
    conversation cannot be removed from it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        conversation_id=conversation_id,
        client=client,
        user_id=user_id,
        agent_id=agent_id,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
) -> Response[Any | ErrorResponse]:
    """Remove Conversation Participant

     Remove one person, or one agent. Name exactly one of user_id or agent_id. The person who opened the
    conversation cannot be removed from it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=agent_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    conversation_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    user_id: None | Unset | UUID = UNSET,
    agent_id: None | Unset | UUID = UNSET,
) -> Any | ErrorResponse | None:
    """Remove Conversation Participant

     Remove one person, or one agent. Name exactly one of user_id or agent_id. The person who opened the
    conversation cannot be removed from it.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        user_id (None | Unset | UUID):
        agent_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            client=client,
            user_id=user_id,
            agent_id=agent_id,
        )
    ).parsed

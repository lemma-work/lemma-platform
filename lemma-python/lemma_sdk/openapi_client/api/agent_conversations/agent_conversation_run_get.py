from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_run_response import AgentRunResponse
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/conversations/{conversation_id}/runs/{run_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            conversation_id=quote(str(conversation_id), safe=""),
            run_id=quote(str(run_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentRunResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentRunResponse.from_dict(response.json())

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
) -> Response[AgentRunResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentRunResponse | ErrorResponse]:
    """Get Conversation Run

     One run of a conversation: its status, output and error, and the model tokens it used (input,
    output, cache reads and writes, and the models). A conversation's latest run is `last_run_id` on the
    conversation; a send's is `agent_run_id` on its answer. 404 for a run of any other conversation.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AgentRunResponse | ErrorResponse | None:
    """Get Conversation Run

     One run of a conversation: its status, output and error, and the model tokens it used (input,
    output, cache reads and writes, and the models). A conversation's latest run is `last_run_id` on the
    conversation; a send's is `agent_run_id` on its answer. 404 for a run of any other conversation.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRunResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        conversation_id=conversation_id,
        run_id=run_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[AgentRunResponse | ErrorResponse]:
    """Get Conversation Run

     One run of a conversation: its status, output and error, and the model tokens it used (input,
    output, cache reads and writes, and the models). A conversation's latest run is `last_run_id` on the
    conversation; a send's is `agent_run_id` on its answer. 404 for a run of any other conversation.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRunResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        conversation_id=conversation_id,
        run_id=run_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> AgentRunResponse | ErrorResponse | None:
    """Get Conversation Run

     One run of a conversation: its status, output and error, and the model tokens it used (input,
    output, cache reads and writes, and the models). A conversation's latest run is `last_run_id` on the
    conversation; a send's is `agent_run_id` on its answer. 404 for a run of any other conversation.

    Args:
        pod_id (UUID):
        conversation_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRunResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            conversation_id=conversation_id,
            run_id=run_id,
            client=client,
        )
    ).parsed

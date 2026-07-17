from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.conversation_list_response import ConversationListResponse
from ...models.conversation_status import ConversationStatus
from ...models.conversation_type import ConversationType
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    agent_name: None | str | Unset = UNSET,
    status: ConversationStatus | None | Unset = UNSET,
    type_: ConversationType | None | Unset = UNSET,
    parent_id: None | Unset | UUID = UNSET,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 20,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_agent_name: None | str | Unset
    if isinstance(agent_name, Unset):
        json_agent_name = UNSET
    else:
        json_agent_name = agent_name
    params["agent_name"] = json_agent_name

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, ConversationStatus):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status

    json_type_: None | str | Unset
    if isinstance(type_, Unset):
        json_type_ = UNSET
    elif isinstance(type_, ConversationType):
        json_type_ = type_.value
    else:
        json_type_ = type_
    params["type"] = json_type_

    json_parent_id: None | str | Unset
    if isinstance(parent_id, Unset):
        json_parent_id = UNSET
    elif isinstance(parent_id, UUID):
        json_parent_id = str(parent_id)
    else:
        json_parent_id = parent_id
    params["parent_id"] = json_parent_id

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/conversations".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ConversationListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ConversationListResponse.from_dict(response.json())

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
) -> Response[ConversationListResponse | ErrorResponse]:
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
    status: ConversationStatus | None | Unset = UNSET,
    type_: ConversationType | None | Unset = UNSET,
    parent_id: None | Unset | UUID = UNSET,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 20,
) -> Response[ConversationListResponse | ErrorResponse]:
    """List Pod Agent Conversations

     List root conversations for the current user in a pod. Omit agent_name to list conversations across
    the pod, pass POD_DEFAULT (or pod_default) to list default pod assistant conversations, or pass a
    name to list conversations for a specific pod agent. Child (sub-agent) conversations are omitted by
    default; pass parent_id to list the children of a specific conversation instead.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):
        status (ConversationStatus | None | Unset):
        type_ (ConversationType | None | Unset):
        parent_id (None | Unset | UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 20.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        agent_name=agent_name,
        status=status,
        type_=type_,
        parent_id=parent_id,
        page_token=page_token,
        limit=limit,
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
    status: ConversationStatus | None | Unset = UNSET,
    type_: ConversationType | None | Unset = UNSET,
    parent_id: None | Unset | UUID = UNSET,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 20,
) -> ConversationListResponse | ErrorResponse | None:
    """List Pod Agent Conversations

     List root conversations for the current user in a pod. Omit agent_name to list conversations across
    the pod, pass POD_DEFAULT (or pod_default) to list default pod assistant conversations, or pass a
    name to list conversations for a specific pod agent. Child (sub-agent) conversations are omitted by
    default; pass parent_id to list the children of a specific conversation instead.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):
        status (ConversationStatus | None | Unset):
        type_ (ConversationType | None | Unset):
        parent_id (None | Unset | UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 20.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        agent_name=agent_name,
        status=status,
        type_=type_,
        parent_id=parent_id,
        page_token=page_token,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
    status: ConversationStatus | None | Unset = UNSET,
    type_: ConversationType | None | Unset = UNSET,
    parent_id: None | Unset | UUID = UNSET,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 20,
) -> Response[ConversationListResponse | ErrorResponse]:
    """List Pod Agent Conversations

     List root conversations for the current user in a pod. Omit agent_name to list conversations across
    the pod, pass POD_DEFAULT (or pod_default) to list default pod assistant conversations, or pass a
    name to list conversations for a specific pod agent. Child (sub-agent) conversations are omitted by
    default; pass parent_id to list the children of a specific conversation instead.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):
        status (ConversationStatus | None | Unset):
        type_ (ConversationType | None | Unset):
        parent_id (None | Unset | UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 20.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ConversationListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        agent_name=agent_name,
        status=status,
        type_=type_,
        parent_id=parent_id,
        page_token=page_token,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
    status: ConversationStatus | None | Unset = UNSET,
    type_: ConversationType | None | Unset = UNSET,
    parent_id: None | Unset | UUID = UNSET,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 20,
) -> ConversationListResponse | ErrorResponse | None:
    """List Pod Agent Conversations

     List root conversations for the current user in a pod. Omit agent_name to list conversations across
    the pod, pass POD_DEFAULT (or pod_default) to list default pod assistant conversations, or pass a
    name to list conversations for a specific pod agent. Child (sub-agent) conversations are omitted by
    default; pass parent_id to list the children of a specific conversation instead.

    Args:
        pod_id (UUID):
        agent_name (None | str | Unset):
        status (ConversationStatus | None | Unset):
        type_ (ConversationType | None | Unset):
        parent_id (None | Unset | UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 20.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ConversationListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            agent_name=agent_name,
            status=status,
            type_=type_,
            parent_id=parent_id,
            page_token=page_token,
            limit=limit,
        )
    ).parsed

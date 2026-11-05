from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pending_interaction_list_response import PendingInteractionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

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
        "url": "/pods/{pod_id}/conversations/pending-interactions".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PendingInteractionListResponse | None:
    if response.status_code == 200:
        response_200 = PendingInteractionListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PendingInteractionListResponse]:
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
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | PendingInteractionListResponse]:
    """List Interactions Waiting On You

     List every ask_user question, request_approval and Agent Host permission request waiting on the
    caller, across their own conversations in the pod, sub-agent conversations included and archived
    ones left out. Longest-waiting first. Resolve one through the conversation's approval decision
    endpoint, using tool_call_id as the approval id. A permission request drops off once the Agent Host
    has stopped waiting for it.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PendingInteractionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
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
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | PendingInteractionListResponse | None:
    """List Interactions Waiting On You

     List every ask_user question, request_approval and Agent Host permission request waiting on the
    caller, across their own conversations in the pod, sub-agent conversations included and archived
    ones left out. Longest-waiting first. Resolve one through the conversation's approval decision
    endpoint, using tool_call_id as the approval id. A permission request drops off once the Agent Host
    has stopped waiting for it.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PendingInteractionListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        page_token=page_token,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | PendingInteractionListResponse]:
    """List Interactions Waiting On You

     List every ask_user question, request_approval and Agent Host permission request waiting on the
    caller, across their own conversations in the pod, sub-agent conversations included and archived
    ones left out. Longest-waiting first. Resolve one through the conversation's approval decision
    endpoint, using tool_call_id as the approval id. A permission request drops off once the Agent Host
    has stopped waiting for it.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PendingInteractionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        page_token=page_token,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    page_token: None | str | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | PendingInteractionListResponse | None:
    """List Interactions Waiting On You

     List every ask_user question, request_approval and Agent Host permission request waiting on the
    caller, across their own conversations in the pod, sub-agent conversations included and archived
    ones left out. Longest-waiting first. Resolve one through the conversation's approval decision
    endpoint, using tool_call_id as the approval id. A permission request drops off once the Agent Host
    has stopped waiting for it.

    Args:
        pod_id (UUID):
        page_token (None | str | Unset):
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PendingInteractionListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            page_token=page_token,
            limit=limit,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.group_response import GroupResponse
from ...models.group_start_request import GroupStartRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: GroupStartRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/groups".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | GroupResponse | None:
    if response.status_code == 201:
        response_201 = GroupResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | GroupResponse]:
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
    body: GroupStartRequest,
) -> Response[ErrorResponse | GroupResponse]:
    """Start Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    WhatsApp confirms the group moments later: it comes back ``pending``, and
    its invite link appears once confirmed. Telegram and Slack cannot create
    groups for a bot; add the bot to one of theirs instead.

    Args:
        pod_id (UUID):
        body (GroupStartRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: GroupStartRequest,
) -> ErrorResponse | GroupResponse | None:
    """Start Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    WhatsApp confirms the group moments later: it comes back ``pending``, and
    its invite link appears once confirmed. Telegram and Slack cannot create
    groups for a bot; add the bot to one of theirs instead.

    Args:
        pod_id (UUID):
        body (GroupStartRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: GroupStartRequest,
) -> Response[ErrorResponse | GroupResponse]:
    """Start Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    WhatsApp confirms the group moments later: it comes back ``pending``, and
    its invite link appears once confirmed. Telegram and Slack cannot create
    groups for a bot; add the bot to one of theirs instead.

    Args:
        pod_id (UUID):
        body (GroupStartRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: GroupStartRequest,
) -> ErrorResponse | GroupResponse | None:
    """Start Group

     Start a WhatsApp group with the pod's bot in it, answered for by the caller.

    WhatsApp confirms the group moments later: it comes back ``pending``, and
    its invite link appears once confirmed. Telegram and Slack cannot create
    groups for a bot; add the bot to one of theirs instead.

    Args:
        pod_id (UUID):
        body (GroupStartRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

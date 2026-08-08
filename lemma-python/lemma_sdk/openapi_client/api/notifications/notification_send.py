from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notification_response import NotificationResponse
from ...models.notify_member_request import NotifyMemberRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: NotifyMemberRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/notifications".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NotificationResponse | None:
    if response.status_code == 201:
        response_201 = NotificationResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NotificationResponse]:
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
    body: NotifyMemberRequest,
) -> Response[ErrorResponse | NotificationResponse]:
    """Notify A Pod Member

     Reach a pod member on whichever surface they actually use, leaving a copy in their Lemma inbox
    either way.

    Gated on `conversation.write` rather than an editor permission: this opens a conversation and writes
    a message into it, which is exactly that grant. Requiring `agent.update` is what left the older
    `surface.send` endpoint with no caller in the product.

    A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a failure — the notification
    exists and the inbox has it. Read `undeliverable_reason` to tell the user what to do about it.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Send a notification to one pod member.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
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
    body: NotifyMemberRequest,
) -> ErrorResponse | NotificationResponse | None:
    """Notify A Pod Member

     Reach a pod member on whichever surface they actually use, leaving a copy in their Lemma inbox
    either way.

    Gated on `conversation.write` rather than an editor permission: this opens a conversation and writes
    a message into it, which is exactly that grant. Requiring `agent.update` is what left the older
    `surface.send` endpoint with no caller in the product.

    A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a failure — the notification
    exists and the inbox has it. Read `undeliverable_reason` to tell the user what to do about it.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Send a notification to one pod member.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationResponse
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
    body: NotifyMemberRequest,
) -> Response[ErrorResponse | NotificationResponse]:
    """Notify A Pod Member

     Reach a pod member on whichever surface they actually use, leaving a copy in their Lemma inbox
    either way.

    Gated on `conversation.write` rather than an editor permission: this opens a conversation and writes
    a message into it, which is exactly that grant. Requiring `agent.update` is what left the older
    `surface.send` endpoint with no caller in the product.

    A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a failure — the notification
    exists and the inbox has it. Read `undeliverable_reason` to tell the user what to do about it.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Send a notification to one pod member.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
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
    body: NotifyMemberRequest,
) -> ErrorResponse | NotificationResponse | None:
    """Notify A Pod Member

     Reach a pod member on whichever surface they actually use, leaving a copy in their Lemma inbox
    either way.

    Gated on `conversation.write` rather than an editor permission: this opens a conversation and writes
    a message into it, which is exactly that grant. Requiring `agent.update` is what left the older
    `surface.send` endpoint with no caller in the product.

    A 201 with `delivery_status` of `UNDELIVERABLE` is a success, not a failure — the notification
    exists and the inbox has it. Read `undeliverable_reason` to tell the user what to do about it.

    Args:
        pod_id (UUID):
        body (NotifyMemberRequest): Send a notification to one pod member.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

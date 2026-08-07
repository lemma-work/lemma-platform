from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notification_respond_request import NotificationRespondRequest
from ...models.notification_response import NotificationResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    notification_id: UUID,
    *,
    body: NotificationRespondRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/notifications/{notification_id}/respond".format(
            pod_id=quote(str(pod_id), safe=""),
            notification_id=quote(str(notification_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NotificationResponse | None:
    if response.status_code == 200:
        response_200 = NotificationResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NotificationResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotificationRespondRequest,
) -> Response[ErrorResponse | NotificationResponse]:
    """Respond To A Notification

     Answer a notification from the app. Produces the same `RESPONDED` an agent-mediated reply on a chat
    surface produces, so the asking run sees it either way.

    Returns 409 when the notification is answered by completing its `action` instead — a workflow form
    is submitted through the workflow run endpoint, where it is validated against the node's schema. It
    also returns 409 if somebody already answered it, rather than overwriting an answer that may already
    have been acted on.

    Args:
        pod_id (UUID):
        notification_id (UUID):
        body (NotificationRespondRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        notification_id=notification_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotificationRespondRequest,
) -> ErrorResponse | NotificationResponse | None:
    """Respond To A Notification

     Answer a notification from the app. Produces the same `RESPONDED` an agent-mediated reply on a chat
    surface produces, so the asking run sees it either way.

    Returns 409 when the notification is answered by completing its `action` instead — a workflow form
    is submitted through the workflow run endpoint, where it is validated against the node's schema. It
    also returns 409 if somebody already answered it, rather than overwriting an answer that may already
    have been acted on.

    Args:
        pod_id (UUID):
        notification_id (UUID):
        body (NotificationRespondRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        notification_id=notification_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotificationRespondRequest,
) -> Response[ErrorResponse | NotificationResponse]:
    """Respond To A Notification

     Answer a notification from the app. Produces the same `RESPONDED` an agent-mediated reply on a chat
    surface produces, so the asking run sees it either way.

    Returns 409 when the notification is answered by completing its `action` instead — a workflow form
    is submitted through the workflow run endpoint, where it is validated against the node's schema. It
    also returns 409 if somebody already answered it, rather than overwriting an answer that may already
    have been acted on.

    Args:
        pod_id (UUID):
        notification_id (UUID):
        body (NotificationRespondRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        notification_id=notification_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NotificationRespondRequest,
) -> ErrorResponse | NotificationResponse | None:
    """Respond To A Notification

     Answer a notification from the app. Produces the same `RESPONDED` an agent-mediated reply on a chat
    surface produces, so the asking run sees it either way.

    Returns 409 when the notification is answered by completing its `action` instead — a workflow form
    is submitted through the workflow run endpoint, where it is validated against the node's schema. It
    also returns 409 if somebody already answered it, rather than overwriting an answer that may already
    have been acted on.

    Args:
        pod_id (UUID):
        notification_id (UUID):
        body (NotificationRespondRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            notification_id=notification_id,
            client=client,
            body=body,
        )
    ).parsed

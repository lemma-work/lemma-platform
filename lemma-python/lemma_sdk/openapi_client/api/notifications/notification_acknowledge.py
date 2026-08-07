from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notification_response import NotificationResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    notification_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/notifications/{notification_id}/acknowledge".format(
            pod_id=quote(str(pod_id), safe=""),
            notification_id=quote(str(notification_id), safe=""),
        ),
    }

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
) -> Response[ErrorResponse | NotificationResponse]:
    """Acknowledge A Notification

     Dismiss a notification that asked for nothing. Returns 409 when a response is owed — dismissing a
    question is not answering it.

    Args:
        pod_id (UUID):
        notification_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        notification_id=notification_id,
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
) -> ErrorResponse | NotificationResponse | None:
    """Acknowledge A Notification

     Dismiss a notification that asked for nothing. Returns 409 when a response is owed — dismissing a
    question is not answering it.

    Args:
        pod_id (UUID):
        notification_id (UUID):

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
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | NotificationResponse]:
    """Acknowledge A Notification

     Dismiss a notification that asked for nothing. Returns 409 when a response is owed — dismissing a
    question is not answering it.

    Args:
        pod_id (UUID):
        notification_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        notification_id=notification_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    notification_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | NotificationResponse | None:
    """Acknowledge A Notification

     Dismiss a notification that asked for nothing. Returns 409 when a response is owed — dismissing a
    question is not answering it.

    Args:
        pod_id (UUID):
        notification_id (UUID):

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
        )
    ).parsed

import datetime
from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notification_list_response import NotificationListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    pod_id: None | Unset | UUID = UNSET,
    unread_only: bool | Unset = False,
    limit: int | Unset = 50,
    before: datetime.datetime | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_pod_id: None | str | Unset
    if isinstance(pod_id, Unset):
        json_pod_id = UNSET
    elif isinstance(pod_id, UUID):
        json_pod_id = str(pod_id)
    else:
        json_pod_id = pod_id
    params["pod_id"] = json_pod_id

    params["unread_only"] = unread_only

    params["limit"] = limit

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    elif isinstance(before, datetime.datetime):
        json_before = before.isoformat()
    else:
        json_before = before
    params["before"] = json_before

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/notifications",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NotificationListResponse | None:
    if response.status_code == 200:
        response_200 = NotificationListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | NotificationListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
    unread_only: bool | Unset = False,
    limit: int | Unset = 50,
    before: datetime.datetime | None | Unset = UNSET,
) -> Response[ErrorResponse | NotificationListResponse]:
    """List Notifications

     The current user's notifications, newest first.

    Args:
        pod_id (None | Unset | UUID):
        unread_only (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 50.
        before (datetime.datetime | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
    unread_only: bool | Unset = False,
    limit: int | Unset = 50,
    before: datetime.datetime | None | Unset = UNSET,
) -> ErrorResponse | NotificationListResponse | None:
    """List Notifications

     The current user's notifications, newest first.

    Args:
        pod_id (None | Unset | UUID):
        unread_only (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 50.
        before (datetime.datetime | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationListResponse
    """

    return sync_detailed(
        client=client,
        pod_id=pod_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
    unread_only: bool | Unset = False,
    limit: int | Unset = 50,
    before: datetime.datetime | None | Unset = UNSET,
) -> Response[ErrorResponse | NotificationListResponse]:
    """List Notifications

     The current user's notifications, newest first.

    Args:
        pod_id (None | Unset | UUID):
        unread_only (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 50.
        before (datetime.datetime | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        unread_only=unread_only,
        limit=limit,
        before=before,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    pod_id: None | Unset | UUID = UNSET,
    unread_only: bool | Unset = False,
    limit: int | Unset = 50,
    before: datetime.datetime | None | Unset = UNSET,
) -> ErrorResponse | NotificationListResponse | None:
    """List Notifications

     The current user's notifications, newest first.

    Args:
        pod_id (None | Unset | UUID):
        unread_only (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 50.
        before (datetime.datetime | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            pod_id=pod_id,
            unread_only=unread_only,
            limit=limit,
            before=before,
        )
    ).parsed

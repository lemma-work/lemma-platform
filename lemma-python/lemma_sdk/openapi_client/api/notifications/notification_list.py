from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notification_list_response import NotificationListResponse
from ...models.notification_status import NotificationStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    status: list[NotificationStatus] | None | Unset = UNSET,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_status: list[str] | None | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, list):
        json_status = []
        for status_type_0_item_data in status:
            status_type_0_item = status_type_0_item_data.value
            json_status.append(status_type_0_item)

    else:
        json_status = status
    params["status"] = json_status

    params["limit"] = limit

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/notifications".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
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
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: list[NotificationStatus] | None | Unset = UNSET,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NotificationListResponse]:
    """List My Notifications

     Notifications addressed to the current user in this pod, newest first. Filter with `status`
    (repeatable). Each item carries everything needed to render its action: `awaiting_response` decides
    whether to offer one, and `responds_through_action` decides whether it is a free-text reply or the
    form described by `action`.

    Args:
        pod_id (UUID):
        status (list[NotificationStatus] | None | Unset):
        limit (int | Unset):  Default: 50.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        status=status,
        limit=limit,
        page_token=page_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: list[NotificationStatus] | None | Unset = UNSET,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | NotificationListResponse | None:
    """List My Notifications

     Notifications addressed to the current user in this pod, newest first. Filter with `status`
    (repeatable). Each item carries everything needed to render its action: `awaiting_response` decides
    whether to offer one, and `responds_through_action` decides whether it is a free-text reply or the
    form described by `action`.

    Args:
        pod_id (UUID):
        status (list[NotificationStatus] | None | Unset):
        limit (int | Unset):  Default: 50.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        status=status,
        limit=limit,
        page_token=page_token,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: list[NotificationStatus] | None | Unset = UNSET,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NotificationListResponse]:
    """List My Notifications

     Notifications addressed to the current user in this pod, newest first. Filter with `status`
    (repeatable). Each item carries everything needed to render its action: `awaiting_response` decides
    whether to offer one, and `responds_through_action` decides whether it is a free-text reply or the
    form described by `action`.

    Args:
        pod_id (UUID):
        status (list[NotificationStatus] | None | Unset):
        limit (int | Unset):  Default: 50.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NotificationListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        status=status,
        limit=limit,
        page_token=page_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    status: list[NotificationStatus] | None | Unset = UNSET,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | NotificationListResponse | None:
    """List My Notifications

     Notifications addressed to the current user in this pod, newest first. Filter with `status`
    (repeatable). Each item carries everything needed to render its action: `awaiting_response` decides
    whether to offer one, and `responds_through_action` decides whether it is a free-text reply or the
    form described by `action`.

    Args:
        pod_id (UUID):
        status (list[NotificationStatus] | None | Unset):
        limit (int | Unset):  Default: 50.
        page_token (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NotificationListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            status=status,
            limit=limit,
            page_token=page_token,
        )
    ).parsed

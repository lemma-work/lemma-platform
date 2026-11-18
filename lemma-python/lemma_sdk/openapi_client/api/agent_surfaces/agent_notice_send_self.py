from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.notice_request import NoticeRequest
from ...models.notice_response import NoticeResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/members/me/notices".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | NoticeResponse | None:
    if response.status_code == 201:
        response_201 = NoticeResponse.from_dict(response.json())

        return response_201

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = ErrorResponse.from_dict(response.json())

        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | NoticeResponse]:
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
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NoticeResponse]:
    """Send Self Notice

     Tell the person this request acts for something, in their own channel:
    a function or agent writing to the person it runs as, or a member writing
    a note to themselves. There is no recipient to name. A workload needs
    `channel.notify_self` granted on the pod (`resource_type` `pod`,
    `resource_name` `/`); a member holds it with `channel.write`. Idempotency
    and the per-minute cap are the same as for a notice to another member.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (NoticeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NoticeResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | NoticeResponse | None:
    """Send Self Notice

     Tell the person this request acts for something, in their own channel:
    a function or agent writing to the person it runs as, or a member writing
    a note to themselves. There is no recipient to name. A workload needs
    `channel.notify_self` granted on the pod (`resource_type` `pod`,
    `resource_name` `/`); a member holds it with `channel.write`. Idempotency
    and the per-minute cap are the same as for a notice to another member.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (NoticeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NoticeResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NoticeResponse]:
    """Send Self Notice

     Tell the person this request acts for something, in their own channel:
    a function or agent writing to the person it runs as, or a member writing
    a note to themselves. There is no recipient to name. A workload needs
    `channel.notify_self` granted on the pod (`resource_type` `pod`,
    `resource_name` `/`); a member holds it with `channel.write`. Idempotency
    and the per-minute cap are the same as for a notice to another member.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (NoticeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | NoticeResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | NoticeResponse | None:
    """Send Self Notice

     Tell the person this request acts for something, in their own channel:
    a function or agent writing to the person it runs as, or a member writing
    a note to themselves. There is no recipient to name. A workload needs
    `channel.notify_self` granted on the pod (`resource_type` `pod`,
    `resource_name` `/`); a member holds it with `channel.write`. Idempotency
    and the per-minute cap are the same as for a notice to another member.

    Args:
        pod_id (UUID):
        idempotency_key (None | str | Unset):
        body (NoticeRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | NoticeResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed

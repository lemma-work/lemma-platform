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
    member: str,
    *,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(idempotency_key, Unset):
        headers["Idempotency-Key"] = idempotency_key

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/members/{member}/notices".format(
            pod_id=quote(str(pod_id), safe=""),
            member=quote(str(member), safe=""),
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
    member: str,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NoticeResponse]:
    """Send Notice

     Tell another member of the pod something, in their own channel. The
    same `Idempotency-Key` from the same member is the same notice; sent with
    other text, it is refused (422). A member sends at most
    `NOTICES_PER_MINUTE` a minute (429).

    Args:
        pod_id (UUID):
        member (str): The member to tell: a pod member id, user id, or email address.
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
        member=member,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    member: str,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | NoticeResponse | None:
    """Send Notice

     Tell another member of the pod something, in their own channel. The
    same `Idempotency-Key` from the same member is the same notice; sent with
    other text, it is refused (422). A member sends at most
    `NOTICES_PER_MINUTE` a minute (429).

    Args:
        pod_id (UUID):
        member (str): The member to tell: a pod member id, user id, or email address.
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
        member=member,
        client=client,
        body=body,
        idempotency_key=idempotency_key,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    member: str,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> Response[ErrorResponse | NoticeResponse]:
    """Send Notice

     Tell another member of the pod something, in their own channel. The
    same `Idempotency-Key` from the same member is the same notice; sent with
    other text, it is refused (422). A member sends at most
    `NOTICES_PER_MINUTE` a minute (429).

    Args:
        pod_id (UUID):
        member (str): The member to tell: a pod member id, user id, or email address.
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
        member=member,
        body=body,
        idempotency_key=idempotency_key,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    member: str,
    *,
    client: AuthenticatedClient | Client,
    body: NoticeRequest,
    idempotency_key: None | str | Unset = UNSET,
) -> ErrorResponse | NoticeResponse | None:
    """Send Notice

     Tell another member of the pod something, in their own channel. The
    same `Idempotency-Key` from the same member is the same notice; sent with
    other text, it is refused (422). A member sends at most
    `NOTICES_PER_MINUTE` a minute (429).

    Args:
        pod_id (UUID):
        member (str): The member to tell: a pod member id, user id, or email address.
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
            member=member,
            client=client,
            body=body,
            idempotency_key=idempotency_key,
        )
    ).parsed

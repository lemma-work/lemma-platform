from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.group_link_request import GroupLinkRequest
from ...models.group_link_response import GroupLinkResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: GroupLinkRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/groups/links".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | GroupLinkResponse | None:
    if response.status_code == 200:
        response_200 = GroupLinkResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | GroupLinkResponse]:
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
    body: GroupLinkRequest,
) -> Response[ErrorResponse | GroupLinkResponse]:
    """Group Link

     A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group is then the caller's to answer for. Which Telegram account is
    theirs is still their profile's to say: a link is easily passed on, so the
    one that used it is never taken for them. The link works for an hour.

    Args:
        pod_id (UUID):
        body (GroupLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupLinkResponse]
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
    body: GroupLinkRequest,
) -> ErrorResponse | GroupLinkResponse | None:
    """Group Link

     A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group is then the caller's to answer for. Which Telegram account is
    theirs is still their profile's to say: a link is easily passed on, so the
    one that used it is never taken for them. The link works for an hour.

    Args:
        pod_id (UUID):
        body (GroupLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupLinkResponse
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
    body: GroupLinkRequest,
) -> Response[ErrorResponse | GroupLinkResponse]:
    """Group Link

     A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group is then the caller's to answer for. Which Telegram account is
    theirs is still their profile's to say: a link is easily passed on, so the
    one that used it is never taken for them. The link works for an hour.

    Args:
        pod_id (UUID):
        body (GroupLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupLinkResponse]
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
    body: GroupLinkRequest,
) -> ErrorResponse | GroupLinkResponse | None:
    """Group Link

     A one-use link that adds the pod's Telegram bot to a group the caller picks.

    The group is then the caller's to answer for. Which Telegram account is
    theirs is still their profile's to say: a link is easily passed on, so the
    one that used it is never taken for them. The link works for an hour.

    Args:
        pod_id (UUID):
        body (GroupLinkRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupLinkResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

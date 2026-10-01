from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.group_timeline_response import GroupTimelineResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    group_id: UUID,
    *,
    limit: int | Unset = 60,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/groups/{group_id}/timeline".format(
            pod_id=quote(str(pod_id), safe=""),
            group_id=quote(str(group_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | GroupTimelineResponse | None:
    if response.status_code == 200:
        response_200 = GroupTimelineResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | GroupTimelineResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    group_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 60,
) -> Response[ErrorResponse | GroupTimelineResponse]:
    """Group Timeline

     What was said in the group, oldest first, as far as the pod kept it.

    Kept for WhatsApp and Telegram groups. A Slack channel's history is
    Slack's; it comes back empty here. An answer the bot made with one member's
    own access comes back withheld to everybody else: they may not be able to
    see what it was made from.

    Args:
        pod_id (UUID):
        group_id (UUID):
        limit (int | Unset):  Default: 60.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupTimelineResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        group_id=group_id,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    group_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 60,
) -> ErrorResponse | GroupTimelineResponse | None:
    """Group Timeline

     What was said in the group, oldest first, as far as the pod kept it.

    Kept for WhatsApp and Telegram groups. A Slack channel's history is
    Slack's; it comes back empty here. An answer the bot made with one member's
    own access comes back withheld to everybody else: they may not be able to
    see what it was made from.

    Args:
        pod_id (UUID):
        group_id (UUID):
        limit (int | Unset):  Default: 60.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupTimelineResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        group_id=group_id,
        client=client,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    group_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 60,
) -> Response[ErrorResponse | GroupTimelineResponse]:
    """Group Timeline

     What was said in the group, oldest first, as far as the pod kept it.

    Kept for WhatsApp and Telegram groups. A Slack channel's history is
    Slack's; it comes back empty here. An answer the bot made with one member's
    own access comes back withheld to everybody else: they may not be able to
    see what it was made from.

    Args:
        pod_id (UUID):
        group_id (UUID):
        limit (int | Unset):  Default: 60.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | GroupTimelineResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        group_id=group_id,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    group_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 60,
) -> ErrorResponse | GroupTimelineResponse | None:
    """Group Timeline

     What was said in the group, oldest first, as far as the pod kept it.

    Kept for WhatsApp and Telegram groups. A Slack channel's history is
    Slack's; it comes back empty here. An answer the bot made with one member's
    own access comes back withheld to everybody else: they may not be able to
    see what it was made from.

    Args:
        pod_id (UUID):
        group_id (UUID):
        limit (int | Unset):  Default: 60.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | GroupTimelineResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            group_id=group_id,
            client=client,
            limit=limit,
        )
    ).parsed

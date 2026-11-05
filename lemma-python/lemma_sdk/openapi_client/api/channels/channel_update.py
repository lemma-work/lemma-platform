from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.channel_detail_response import ChannelDetailResponse
from ...models.channel_update_request import ChannelUpdateRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    *,
    body: ChannelUpdateRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/channels/{channel_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ChannelDetailResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = ChannelDetailResponse.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ChannelDetailResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelUpdateRequest,
) -> Response[ChannelDetailResponse | ErrorResponse]:
    """Update Channel

     Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived`, `answers_shared`
    (whether agents' answers made with the asker's access are read by
    everybody here, or kept to the asker until they share one) -- its
    owners' or an admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        body (ChannelUpdateRequest): Absent is unchanged; ``null`` clears where clearing means
            something.

            A group takes ``title``, ``topic``, ``visibility``, ``archived``,
            ``answers_shared``. A contact's channel takes ``agents_answer``,
            ``hand_back_after_hours``,
            ``status``, ``assignee`` (and needs ``contact.message``). Anything else
            for the kind is 409.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelUpdateRequest,
) -> ChannelDetailResponse | ErrorResponse | None:
    """Update Channel

     Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived`, `answers_shared`
    (whether agents' answers made with the asker's access are read by
    everybody here, or kept to the asker until they share one) -- its
    owners' or an admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        body (ChannelUpdateRequest): Absent is unchanged; ``null`` clears where clearing means
            something.

            A group takes ``title``, ``topic``, ``visibility``, ``archived``,
            ``answers_shared``. A contact's channel takes ``agents_answer``,
            ``hand_back_after_hours``,
            ``status``, ``assignee`` (and needs ``contact.message``). Anything else
            for the kind is 409.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelDetailResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelUpdateRequest,
) -> Response[ChannelDetailResponse | ErrorResponse]:
    """Update Channel

     Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived`, `answers_shared`
    (whether agents' answers made with the asker's access are read by
    everybody here, or kept to the asker until they share one) -- its
    owners' or an admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        body (ChannelUpdateRequest): Absent is unchanged; ``null`` clears where clearing means
            something.

            A group takes ``title``, ``topic``, ``visibility``, ``archived``,
            ``answers_shared``. A contact's channel takes ``agents_answer``,
            ``hand_back_after_hours``,
            ``status``, ``assignee`` (and needs ``contact.message``). Anything else
            for the kind is 409.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChannelDetailResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ChannelUpdateRequest,
) -> ChannelDetailResponse | ErrorResponse | None:
    """Update Channel

     Change exactly the fields named; `null` clears.

    A group: `title`, `topic`, `visibility`, `archived`, `answers_shared`
    (whether agents' answers made with the asker's access are read by
    everybody here, or kept to the asker until they share one) -- its
    owners' or an admin's. A contact's channel: `agents_answer`, `hand_back_after_hours`,
    `status`, `assignee` -- for who may write to contacts. Switching the agent
    on hands the contact back to it now: nobody is assigned.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        body (ChannelUpdateRequest): Absent is unchanged; ``null`` clears where clearing means
            something.

            A group takes ``title``, ``topic``, ``visibility``, ``archived``,
            ``answers_shared``. A contact's channel takes ``agents_answer``,
            ``hand_back_after_hours``,
            ``status``, ``assignee`` (and needs ``contact.message``). Anything else
            for the kind is 409.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChannelDetailResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            client=client,
            body=body,
        )
    ).parsed

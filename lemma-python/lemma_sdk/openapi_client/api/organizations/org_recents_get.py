from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.organization_recents_response import OrganizationRecentsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    organization_id: UUID,
    *,
    days: int | Unset = 14,
    chats: int | Unset = 10,
    timezone: str | Unset = "UTC",
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["days"] = days

    params["chats"] = chats

    params["timezone"] = timezone

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/organizations/{organization_id}/recents".format(
            organization_id=quote(str(organization_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OrganizationRecentsResponse | None:
    if response.status_code == 200:
        response_200 = OrganizationRecentsResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OrganizationRecentsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    chats: int | Unset = 10,
    timezone: str | Unset = "UTC",
) -> Response[ErrorResponse | OrganizationRecentsResponse]:
    """Get What Moved Lately In Each Pod

     For every pod the current user may open in the organization, quiet ones included: their latest chats
    (conversations of no channel that they talk in, or that wait on them), the workflows and schedules
    that ran in the window -- one each, with the newest run and a count -- and a count per day of their
    conversations by the day each was last active, split into started by an agent (a schedule, a
    workflow, a notification, a task or another pod's ask) and by a person. Days are calendar days in
    `timezone`, the window ending today.

    Args:
        organization_id (UUID):
        days (int | Unset):  Default: 14.
        chats (int | Unset): Chats listed per pod. Default: 10.
        timezone (str | Unset): IANA time zone the days are counted in, e.g. Europe/Berlin.
            Default: 'UTC'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationRecentsResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        days=days,
        chats=chats,
        timezone=timezone,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    chats: int | Unset = 10,
    timezone: str | Unset = "UTC",
) -> ErrorResponse | OrganizationRecentsResponse | None:
    """Get What Moved Lately In Each Pod

     For every pod the current user may open in the organization, quiet ones included: their latest chats
    (conversations of no channel that they talk in, or that wait on them), the workflows and schedules
    that ran in the window -- one each, with the newest run and a count -- and a count per day of their
    conversations by the day each was last active, split into started by an agent (a schedule, a
    workflow, a notification, a task or another pod's ask) and by a person. Days are calendar days in
    `timezone`, the window ending today.

    Args:
        organization_id (UUID):
        days (int | Unset):  Default: 14.
        chats (int | Unset): Chats listed per pod. Default: 10.
        timezone (str | Unset): IANA time zone the days are counted in, e.g. Europe/Berlin.
            Default: 'UTC'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationRecentsResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        client=client,
        days=days,
        chats=chats,
        timezone=timezone,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    chats: int | Unset = 10,
    timezone: str | Unset = "UTC",
) -> Response[ErrorResponse | OrganizationRecentsResponse]:
    """Get What Moved Lately In Each Pod

     For every pod the current user may open in the organization, quiet ones included: their latest chats
    (conversations of no channel that they talk in, or that wait on them), the workflows and schedules
    that ran in the window -- one each, with the newest run and a count -- and a count per day of their
    conversations by the day each was last active, split into started by an agent (a schedule, a
    workflow, a notification, a task or another pod's ask) and by a person. Days are calendar days in
    `timezone`, the window ending today.

    Args:
        organization_id (UUID):
        days (int | Unset):  Default: 14.
        chats (int | Unset): Chats listed per pod. Default: 10.
        timezone (str | Unset): IANA time zone the days are counted in, e.g. Europe/Berlin.
            Default: 'UTC'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OrganizationRecentsResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        days=days,
        chats=chats,
        timezone=timezone,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    days: int | Unset = 14,
    chats: int | Unset = 10,
    timezone: str | Unset = "UTC",
) -> ErrorResponse | OrganizationRecentsResponse | None:
    """Get What Moved Lately In Each Pod

     For every pod the current user may open in the organization, quiet ones included: their latest chats
    (conversations of no channel that they talk in, or that wait on them), the workflows and schedules
    that ran in the window -- one each, with the newest run and a count -- and a count per day of their
    conversations by the day each was last active, split into started by an agent (a schedule, a
    workflow, a notification, a task or another pod's ask) and by a person. Days are calendar days in
    `timezone`, the window ending today.

    Args:
        organization_id (UUID):
        days (int | Unset):  Default: 14.
        chats (int | Unset): Chats listed per pod. Default: 10.
        timezone (str | Unset): IANA time zone the days are counted in, e.g. Europe/Berlin.
            Default: 'UTC'.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OrganizationRecentsResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            client=client,
            days=days,
            chats=chats,
            timezone=timezone,
        )
    ).parsed

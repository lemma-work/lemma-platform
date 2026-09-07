import datetime
from http import HTTPStatus
from typing import Any
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.usage_list_response import UsageListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    organization_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 50,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_organization_id: None | str | Unset
    if isinstance(organization_id, Unset):
        json_organization_id = UNSET
    elif isinstance(organization_id, UUID):
        json_organization_id = str(organization_id)
    else:
        json_organization_id = organization_id
    params["organization_id"] = json_organization_id

    json_start: None | str | Unset
    if isinstance(start, Unset):
        json_start = UNSET
    elif isinstance(start, datetime.datetime):
        json_start = start.isoformat()
    else:
        json_start = start
    params["start"] = json_start

    json_end: None | str | Unset
    if isinstance(end, Unset):
        json_end = UNSET
    elif isinstance(end, datetime.datetime):
        json_end = end.isoformat()
    else:
        json_end = end
    params["end"] = json_end

    params["days"] = days

    params["limit"] = limit

    json_agent_run_id: None | str | Unset
    if isinstance(agent_run_id, Unset):
        json_agent_run_id = UNSET
    elif isinstance(agent_run_id, UUID):
        json_agent_run_id = str(agent_run_id)
    else:
        json_agent_run_id = agent_run_id
    params["agent_run_id"] = json_agent_run_id

    json_conversation_id: None | str | Unset
    if isinstance(conversation_id, Unset):
        json_conversation_id = UNSET
    elif isinstance(conversation_id, UUID):
        json_conversation_id = str(conversation_id)
    else:
        json_conversation_id = conversation_id
    params["conversation_id"] = json_conversation_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/usage/me/events",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | UsageListResponse | None:
    if response.status_code == 200:
        response_200 = UsageListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | UsageListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    organization_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 50,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | UsageListResponse]:
    """My Events

    Args:
        organization_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 50.
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UsageListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    organization_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 50,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
) -> ErrorResponse | UsageListResponse | None:
    """My Events

    Args:
        organization_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 50.
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UsageListResponse
    """

    return sync_detailed(
        client=client,
        organization_id=organization_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    organization_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 50,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
) -> Response[ErrorResponse | UsageListResponse]:
    """My Events

    Args:
        organization_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 50.
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UsageListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        start=start,
        end=end,
        days=days,
        limit=limit,
        agent_run_id=agent_run_id,
        conversation_id=conversation_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    organization_id: None | Unset | UUID = UNSET,
    start: datetime.datetime | None | Unset = UNSET,
    end: datetime.datetime | None | Unset = UNSET,
    days: int | Unset = 30,
    limit: int | Unset = 50,
    agent_run_id: None | Unset | UUID = UNSET,
    conversation_id: None | Unset | UUID = UNSET,
) -> ErrorResponse | UsageListResponse | None:
    """My Events

    Args:
        organization_id (None | Unset | UUID):
        start (datetime.datetime | None | Unset):
        end (datetime.datetime | None | Unset):
        days (int | Unset):  Default: 30.
        limit (int | Unset):  Default: 50.
        agent_run_id (None | Unset | UUID):
        conversation_id (None | Unset | UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UsageListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            organization_id=organization_id,
            start=start,
            end=end,
            days=days,
            limit=limit,
            agent_run_id=agent_run_id,
            conversation_id=conversation_id,
        )
    ).parsed

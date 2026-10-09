from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.schedule_run_list_response import ScheduleRunListResponse
from ...models.schedule_run_status import ScheduleRunStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    schedule_id: UUID,
    *,
    limit: int | Unset = 100,
    status: None | ScheduleRunStatus | Unset = UNSET,
    skipped: bool | None | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    elif isinstance(status, ScheduleRunStatus):
        json_status = status.value
    else:
        json_status = status
    params["status"] = json_status

    json_skipped: bool | None | Unset
    if isinstance(skipped, Unset):
        json_skipped = UNSET
    else:
        json_skipped = skipped
    params["skipped"] = json_skipped

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/schedules/{schedule_id}/runs".format(
            pod_id=quote(str(pod_id), safe=""),
            schedule_id=quote(str(schedule_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ScheduleRunListResponse | None:
    if response.status_code == 200:
        response_200 = ScheduleRunListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ScheduleRunListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    status: None | ScheduleRunStatus | Unset = UNSET,
    skipped: bool | None | Unset = UNSET,
) -> Response[ErrorResponse | ScheduleRunListResponse]:
    """List Schedule Runs

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        limit (int | Unset):  Default: 100.
        status (None | ScheduleRunStatus | Unset): Only runs that report this status -- the
            target's outcome once there is one.
        skipped (bool | None | Unset): true: only events the schedule's filter skipped. false:
            leave them out, which is what a busy webhook schedule's history usually needs. Omitted:
            both.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleRunListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        limit=limit,
        status=status,
        skipped=skipped,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    schedule_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    status: None | ScheduleRunStatus | Unset = UNSET,
    skipped: bool | None | Unset = UNSET,
) -> ErrorResponse | ScheduleRunListResponse | None:
    """List Schedule Runs

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        limit (int | Unset):  Default: 100.
        status (None | ScheduleRunStatus | Unset): Only runs that report this status -- the
            target's outcome once there is one.
        skipped (bool | None | Unset): true: only events the schedule's filter skipped. false:
            leave them out, which is what a busy webhook schedule's history usually needs. Omitted:
            both.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleRunListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        schedule_id=schedule_id,
        client=client,
        limit=limit,
        status=status,
        skipped=skipped,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    status: None | ScheduleRunStatus | Unset = UNSET,
    skipped: bool | None | Unset = UNSET,
) -> Response[ErrorResponse | ScheduleRunListResponse]:
    """List Schedule Runs

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        limit (int | Unset):  Default: 100.
        status (None | ScheduleRunStatus | Unset): Only runs that report this status -- the
            target's outcome once there is one.
        skipped (bool | None | Unset): true: only events the schedule's filter skipped. false:
            leave them out, which is what a busy webhook schedule's history usually needs. Omitted:
            both.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleRunListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        limit=limit,
        status=status,
        skipped=skipped,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    schedule_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    status: None | ScheduleRunStatus | Unset = UNSET,
    skipped: bool | None | Unset = UNSET,
) -> ErrorResponse | ScheduleRunListResponse | None:
    """List Schedule Runs

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        limit (int | Unset):  Default: 100.
        status (None | ScheduleRunStatus | Unset): Only runs that report this status -- the
            target's outcome once there is one.
        skipped (bool | None | Unset): true: only events the schedule's filter skipped. false:
            leave them out, which is what a busy webhook schedule's history usually needs. Omitted:
            both.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleRunListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            schedule_id=schedule_id,
            client=client,
            limit=limit,
            status=status,
            skipped=skipped,
        )
    ).parsed

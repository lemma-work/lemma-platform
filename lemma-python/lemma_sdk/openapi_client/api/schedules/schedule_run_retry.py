from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.schedule_run_response import ScheduleRunResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/schedules/{schedule_id}/runs/{run_id}/retry".format(
            pod_id=quote(str(pod_id), safe=""),
            schedule_id=quote(str(schedule_id), safe=""),
            run_id=quote(str(run_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ScheduleRunResponse | None:
    if response.status_code == 202:
        response_202 = ScheduleRunResponse.from_dict(response.json())

        return response_202

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ScheduleRunResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ScheduleRunResponse]:
    """Retry Schedule Run

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        run_id=run_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ScheduleRunResponse | None:
    """Retry Schedule Run

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleRunResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        schedule_id=schedule_id,
        run_id=run_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | ScheduleRunResponse]:
    """Retry Schedule Run

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScheduleRunResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        schedule_id=schedule_id,
        run_id=run_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    schedule_id: UUID,
    run_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | ScheduleRunResponse | None:
    """Retry Schedule Run

    Args:
        pod_id (UUID):
        schedule_id (UUID):
        run_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScheduleRunResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            schedule_id=schedule_id,
            run_id=run_id,
            client=client,
        )
    ).parsed

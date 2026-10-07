import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.scorecard_rows_response import ScorecardRowsResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    key: str,
    *,
    end: datetime.date | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_end: None | str | Unset
    if isinstance(end, Unset):
        json_end = UNSET
    elif isinstance(end, datetime.date):
        json_end = end.isoformat()
    else:
        json_end = end
    params["end"] = json_end

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/scorecard/measures/{key}/rows".format(
            pod_id=quote(str(pod_id), safe=""),
            key=quote(str(key), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ScorecardRowsResponse | None:
    if response.status_code == 200:
        response_200 = ScorecardRowsResponse.from_dict(response.json())

        return response_200

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | ScorecardRowsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    key: str,
    *,
    client: AuthenticatedClient | Client,
    end: datetime.date | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | ScorecardRowsResponse]:
    """List Rows Behind A Scorecard Measure

     The units of work a measure counted for the week ending `end` (default today), with whether each
    passed its test. Only a `work` measure has rows; any other is answered 422.

    Args:
        pod_id (UUID):
        key (str):
        end (datetime.date | None | Unset): The day the week ends, not included. Defaults to
            today.
        limit (int | Unset): How many rows to list at most. Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScorecardRowsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        key=key,
        end=end,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    key: str,
    *,
    client: AuthenticatedClient | Client,
    end: datetime.date | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | ScorecardRowsResponse | None:
    """List Rows Behind A Scorecard Measure

     The units of work a measure counted for the week ending `end` (default today), with whether each
    passed its test. Only a `work` measure has rows; any other is answered 422.

    Args:
        pod_id (UUID):
        key (str):
        end (datetime.date | None | Unset): The day the week ends, not included. Defaults to
            today.
        limit (int | Unset): How many rows to list at most. Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScorecardRowsResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        key=key,
        client=client,
        end=end,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    key: str,
    *,
    client: AuthenticatedClient | Client,
    end: datetime.date | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[ErrorResponse | ScorecardRowsResponse]:
    """List Rows Behind A Scorecard Measure

     The units of work a measure counted for the week ending `end` (default today), with whether each
    passed its test. Only a `work` measure has rows; any other is answered 422.

    Args:
        pod_id (UUID):
        key (str):
        end (datetime.date | None | Unset): The day the week ends, not included. Defaults to
            today.
        limit (int | Unset): How many rows to list at most. Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScorecardRowsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        key=key,
        end=end,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    key: str,
    *,
    client: AuthenticatedClient | Client,
    end: datetime.date | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> ErrorResponse | ScorecardRowsResponse | None:
    """List Rows Behind A Scorecard Measure

     The units of work a measure counted for the week ending `end` (default today), with whether each
    passed its test. Only a `work` measure has rows; any other is answered 422.

    Args:
        pod_id (UUID):
        key (str):
        end (datetime.date | None | Unset): The day the week ends, not included. Defaults to
            today.
        limit (int | Unset): How many rows to list at most. Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScorecardRowsResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            key=key,
            client=client,
            end=end,
            limit=limit,
        )
    ).parsed

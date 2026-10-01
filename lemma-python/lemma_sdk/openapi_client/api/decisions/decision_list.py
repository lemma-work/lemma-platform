import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.decision_list_response import DecisionListResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    decider: None | str | Unset = UNSET,
    open_only: bool | Unset = False,
    before: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_decider: None | str | Unset
    if isinstance(decider, Unset):
        json_decider = UNSET
    else:
        json_decider = decider
    params["decider"] = json_decider

    params["open_only"] = open_only

    json_before: None | str | Unset
    if isinstance(before, Unset):
        json_before = UNSET
    elif isinstance(before, datetime.datetime):
        json_before = before.isoformat()
    else:
        json_before = before
    params["before"] = json_before

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/decisions".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DecisionListResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DecisionListResponse.from_dict(response.json())

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
) -> Response[DecisionListResponse | ErrorResponse]:
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
    decider: None | str | Unset = UNSET,
    open_only: bool | Unset = False,
    before: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[DecisionListResponse | ErrorResponse]:
    """List decisions

     Decisions in this pod, newest first: the ones shared with the pod and your own.

    Args:
        pod_id (UUID):
        decider (None | str | Unset): Only this decider's decisions.
        open_only (bool | Unset): Only decisions with a question left open. Default: False.
        before (datetime.datetime | None | Unset): Only decisions made before this time.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DecisionListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider=decider,
        open_only=open_only,
        before=before,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    decider: None | str | Unset = UNSET,
    open_only: bool | Unset = False,
    before: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> DecisionListResponse | ErrorResponse | None:
    """List decisions

     Decisions in this pod, newest first: the ones shared with the pod and your own.

    Args:
        pod_id (UUID):
        decider (None | str | Unset): Only this decider's decisions.
        open_only (bool | Unset): Only decisions with a question left open. Default: False.
        before (datetime.datetime | None | Unset): Only decisions made before this time.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DecisionListResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        decider=decider,
        open_only=open_only,
        before=before,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    decider: None | str | Unset = UNSET,
    open_only: bool | Unset = False,
    before: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> Response[DecisionListResponse | ErrorResponse]:
    """List decisions

     Decisions in this pod, newest first: the ones shared with the pod and your own.

    Args:
        pod_id (UUID):
        decider (None | str | Unset): Only this decider's decisions.
        open_only (bool | Unset): Only decisions with a question left open. Default: False.
        before (datetime.datetime | None | Unset): Only decisions made before this time.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DecisionListResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        decider=decider,
        open_only=open_only,
        before=before,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    decider: None | str | Unset = UNSET,
    open_only: bool | Unset = False,
    before: datetime.datetime | None | Unset = UNSET,
    limit: int | Unset = 50,
) -> DecisionListResponse | ErrorResponse | None:
    """List decisions

     Decisions in this pod, newest first: the ones shared with the pod and your own.

    Args:
        pod_id (UUID):
        decider (None | str | Unset): Only this decider's decisions.
        open_only (bool | Unset): Only decisions with a question left open. Default: False.
        before (datetime.datetime | None | Unset): Only decisions made before this time.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DecisionListResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            decider=decider,
            open_only=open_only,
            before=before,
            limit=limit,
        )
    ).parsed

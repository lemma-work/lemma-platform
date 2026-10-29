from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.outcome_history_response import OutcomeHistoryResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    weeks: int | Unset = 8,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["weeks"] = weeks

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/outcome/history".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OutcomeHistoryResponse | None:
    if response.status_code == 200:
        response_200 = OutcomeHistoryResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OutcomeHistoryResponse]:
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
    weeks: int | Unset = 8,
) -> Response[ErrorResponse | OutcomeHistoryResponse]:
    """Where the pod has stood, week by week

     The last `weeks` weeks of the pod's measure, each on its last reading. The measure is read on a
    schedule and when it is set; a week it was never read in has a null value.

    Args:
        pod_id (UUID):
        weeks (int | Unset):  Default: 8.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeHistoryResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        weeks=weeks,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    weeks: int | Unset = 8,
) -> ErrorResponse | OutcomeHistoryResponse | None:
    """Where the pod has stood, week by week

     The last `weeks` weeks of the pod's measure, each on its last reading. The measure is read on a
    schedule and when it is set; a week it was never read in has a null value.

    Args:
        pod_id (UUID):
        weeks (int | Unset):  Default: 8.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeHistoryResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        weeks=weeks,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    weeks: int | Unset = 8,
) -> Response[ErrorResponse | OutcomeHistoryResponse]:
    """Where the pod has stood, week by week

     The last `weeks` weeks of the pod's measure, each on its last reading. The measure is read on a
    schedule and when it is set; a week it was never read in has a null value.

    Args:
        pod_id (UUID):
        weeks (int | Unset):  Default: 8.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeHistoryResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        weeks=weeks,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    weeks: int | Unset = 8,
) -> ErrorResponse | OutcomeHistoryResponse | None:
    """Where the pod has stood, week by week

     The last `weeks` weeks of the pod's measure, each on its last reading. The measure is read on a
    schedule and when it is set; a week it was never read in has a null value.

    Args:
        pod_id (UUID):
        weeks (int | Unset):  Default: 8.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeHistoryResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            weeks=weeks,
        )
    ).parsed

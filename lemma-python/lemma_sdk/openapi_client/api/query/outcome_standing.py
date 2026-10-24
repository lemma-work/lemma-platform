from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.outcome_standing_response import OutcomeStandingResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/outcome/standing".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OutcomeStandingResponse | None:
    if response.status_code == 200:
        response_200 = OutcomeStandingResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OutcomeStandingResponse]:
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
) -> Response[ErrorResponse | OutcomeStandingResponse]:
    """Where the pod stands

     What the pod owns and what its measure answers now. The measure is read over every row of the tables
    it names, so every member sees the same number, including members who cannot open some of those
    rows; only someone who can read all of them may set it. A measure that cannot be read comes back as
    a `problem` rather than an error.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeStandingResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | OutcomeStandingResponse | None:
    """Where the pod stands

     What the pod owns and what its measure answers now. The measure is read over every row of the tables
    it names, so every member sees the same number, including members who cannot open some of those
    rows; only someone who can read all of them may set it. A measure that cannot be read comes back as
    a `problem` rather than an error.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeStandingResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | OutcomeStandingResponse]:
    """Where the pod stands

     What the pod owns and what its measure answers now. The measure is read over every row of the tables
    it names, so every member sees the same number, including members who cannot open some of those
    rows; only someone who can read all of them may set it. A measure that cannot be read comes back as
    a `problem` rather than an error.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeStandingResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | OutcomeStandingResponse | None:
    """Where the pod stands

     What the pod owns and what its measure answers now. The measure is read over every row of the tables
    it names, so every member sees the same number, including members who cannot open some of those
    rows; only someone who can read all of them may set it. A measure that cannot be read comes back as
    a `problem` rather than an error.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeStandingResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
        )
    ).parsed

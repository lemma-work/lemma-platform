from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.outcome_input import OutcomeInput
from ...models.outcome_set_response import OutcomeSetResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: OutcomeInput,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/outcome".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | OutcomeSetResponse | None:
    if response.status_code == 200:
        response_200 = OutcomeSetResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | OutcomeSetResponse]:
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
    body: OutcomeInput,
) -> Response[ErrorResponse | OutcomeSetResponse]:
    """Set what the pod owns

     Set what the pod owns, replacing what it had: a bar held every week (kind=week) or a finish line by
    a date (kind=date), with the target and unit it is held to and the measure that counts it. A measure
    is one read-only SQL query over the pod's tables that answers a single number, read for every member
    alike; a new or changed one is read once before it is kept, and refused when the caller cannot read
    every row it counts (403), names a table that is not there (404), or does not answer one number
    (422).

    Args:
        pod_id (UUID):
        body (OutcomeInput): What the pod owns, as a person or its agent sets it.

            The fields are the front matter of the plan file the workspace keeps at
            ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
            ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
            them. The plan's prose (what must be true, how the work is done, who holds
            what, what never happens) stays in the file.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeSetResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: OutcomeInput,
) -> ErrorResponse | OutcomeSetResponse | None:
    """Set what the pod owns

     Set what the pod owns, replacing what it had: a bar held every week (kind=week) or a finish line by
    a date (kind=date), with the target and unit it is held to and the measure that counts it. A measure
    is one read-only SQL query over the pod's tables that answers a single number, read for every member
    alike; a new or changed one is read once before it is kept, and refused when the caller cannot read
    every row it counts (403), names a table that is not there (404), or does not answer one number
    (422).

    Args:
        pod_id (UUID):
        body (OutcomeInput): What the pod owns, as a person or its agent sets it.

            The fields are the front matter of the plan file the workspace keeps at
            ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
            ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
            them. The plan's prose (what must be true, how the work is done, who holds
            what, what never happens) stays in the file.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeSetResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: OutcomeInput,
) -> Response[ErrorResponse | OutcomeSetResponse]:
    """Set what the pod owns

     Set what the pod owns, replacing what it had: a bar held every week (kind=week) or a finish line by
    a date (kind=date), with the target and unit it is held to and the measure that counts it. A measure
    is one read-only SQL query over the pod's tables that answers a single number, read for every member
    alike; a new or changed one is read once before it is kept, and refused when the caller cannot read
    every row it counts (403), names a table that is not there (404), or does not answer one number
    (422).

    Args:
        pod_id (UUID):
        body (OutcomeInput): What the pod owns, as a person or its agent sets it.

            The fields are the front matter of the plan file the workspace keeps at
            ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
            ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
            them. The plan's prose (what must be true, how the work is done, who holds
            what, what never happens) stays in the file.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | OutcomeSetResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: OutcomeInput,
) -> ErrorResponse | OutcomeSetResponse | None:
    """Set what the pod owns

     Set what the pod owns, replacing what it had: a bar held every week (kind=week) or a finish line by
    a date (kind=date), with the target and unit it is held to and the measure that counts it. A measure
    is one read-only SQL query over the pod's tables that answers a single number, read for every member
    alike; a new or changed one is read once before it is kept, and refused when the caller cannot read
    every row it counts (403), names a table that is not there (404), or does not answer one number
    (422).

    Args:
        pod_id (UUID):
        body (OutcomeInput): What the pod owns, as a person or its agent sets it.

            The fields are the front matter of the plan file the workspace keeps at
            ``/plan.md`` -- ``outcome`` (here ``sentence``), ``kind``, ``target``,
            ``unit``, ``due`` and ``measure`` -- held here so there is one copy of
            them. The plan's prose (what must be true, how the work is done, who holds
            what, what never happens) stays in the file.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | OutcomeSetResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

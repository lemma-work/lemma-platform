from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.scorecard_preview_request import ScorecardPreviewRequest
from ...models.scorecard_preview_response import ScorecardPreviewResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: ScorecardPreviewRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/scorecard/preview".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ScorecardPreviewResponse | None:
    if response.status_code == 200:
        response_200 = ScorecardPreviewResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ScorecardPreviewResponse]:
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
    body: ScorecardPreviewRequest,
) -> Response[ErrorResponse | ScorecardPreviewResponse]:
    """Preview Scorecard Weeks

     Count scorecard measures over recent weeks without recording anything: the same seven-day windows
    and the same counting the weekly review uses, oldest week first. With `measure`, one unsaved measure
    (a dry run); with `keys`, those saved measures; with neither, every measure that is on and every
    proposal not yet kept. Counted as the caller.

    Args:
        pod_id (UUID):
        body (ScorecardPreviewRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScorecardPreviewResponse]
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
    body: ScorecardPreviewRequest,
) -> ErrorResponse | ScorecardPreviewResponse | None:
    """Preview Scorecard Weeks

     Count scorecard measures over recent weeks without recording anything: the same seven-day windows
    and the same counting the weekly review uses, oldest week first. With `measure`, one unsaved measure
    (a dry run); with `keys`, those saved measures; with neither, every measure that is on and every
    proposal not yet kept. Counted as the caller.

    Args:
        pod_id (UUID):
        body (ScorecardPreviewRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScorecardPreviewResponse
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
    body: ScorecardPreviewRequest,
) -> Response[ErrorResponse | ScorecardPreviewResponse]:
    """Preview Scorecard Weeks

     Count scorecard measures over recent weeks without recording anything: the same seven-day windows
    and the same counting the weekly review uses, oldest week first. With `measure`, one unsaved measure
    (a dry run); with `keys`, those saved measures; with neither, every measure that is on and every
    proposal not yet kept. Counted as the caller.

    Args:
        pod_id (UUID):
        body (ScorecardPreviewRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ScorecardPreviewResponse]
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
    body: ScorecardPreviewRequest,
) -> ErrorResponse | ScorecardPreviewResponse | None:
    """Preview Scorecard Weeks

     Count scorecard measures over recent weeks without recording anything: the same seven-day windows
    and the same counting the weekly review uses, oldest week first. With `measure`, one unsaved measure
    (a dry run); with `keys`, those saved measures; with neither, every measure that is on and every
    proposal not yet kept. Counted as the caller.

    Args:
        pod_id (UUID):
        body (ScorecardPreviewRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ScorecardPreviewResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

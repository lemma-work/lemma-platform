from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.export_start_request import ExportStartRequest
from ...models.export_status_response import ExportStatusResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: ExportStartRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/bundle/exports".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ExportStatusResponse | None:
    if response.status_code == 202:
        response_202 = ExportStatusResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ExportStatusResponse]:
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
    body: ExportStartRequest,
) -> Response[ErrorResponse | ExportStatusResponse]:
    """Start Pod Export

     Enqueue a pod export. Returns 202 with an export_id; poll the status endpoint until READY, then
    download the bundle archive.

    Args:
        pod_id (UUID):
        body (ExportStartRequest): Body for starting a pod export.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ExportStatusResponse]
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
    body: ExportStartRequest,
) -> ErrorResponse | ExportStatusResponse | None:
    """Start Pod Export

     Enqueue a pod export. Returns 202 with an export_id; poll the status endpoint until READY, then
    download the bundle archive.

    Args:
        pod_id (UUID):
        body (ExportStartRequest): Body for starting a pod export.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ExportStatusResponse
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
    body: ExportStartRequest,
) -> Response[ErrorResponse | ExportStatusResponse]:
    """Start Pod Export

     Enqueue a pod export. Returns 202 with an export_id; poll the status endpoint until READY, then
    download the bundle archive.

    Args:
        pod_id (UUID):
        body (ExportStartRequest): Body for starting a pod export.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ExportStatusResponse]
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
    body: ExportStartRequest,
) -> ErrorResponse | ExportStatusResponse | None:
    """Start Pod Export

     Enqueue a pod export. Returns 202 with an export_id; poll the status endpoint until READY, then
    download the bundle archive.

    Args:
        pod_id (UUID):
        body (ExportStartRequest): Body for starting a pod export.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ExportStatusResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_pod_bundle_import_start import BodyPodBundleImportStart
from ...models.error_response import ErrorResponse
from ...models.import_status_response import ImportStatusResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: BodyPodBundleImportStart,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/bundle/imports".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["files"] = body.to_multipart()

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ImportStatusResponse | None:
    if response.status_code == 202:
        response_202 = ImportStatusResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ImportStatusResponse]:
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
    body: BodyPodBundleImportStart,
) -> Response[ErrorResponse | ImportStatusResponse]:
    """Start Pod Import

     Upload a pod bundle (.zip) and enqueue planning. Returns 202 with an import_id; poll the status
    endpoint until AWAITING_CONFIRMATION, review the plan, then apply.

    Args:
        pod_id (UUID):
        body (BodyPodBundleImportStart):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ImportStatusResponse]
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
    body: BodyPodBundleImportStart,
) -> ErrorResponse | ImportStatusResponse | None:
    """Start Pod Import

     Upload a pod bundle (.zip) and enqueue planning. Returns 202 with an import_id; poll the status
    endpoint until AWAITING_CONFIRMATION, review the plan, then apply.

    Args:
        pod_id (UUID):
        body (BodyPodBundleImportStart):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ImportStatusResponse
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
    body: BodyPodBundleImportStart,
) -> Response[ErrorResponse | ImportStatusResponse]:
    """Start Pod Import

     Upload a pod bundle (.zip) and enqueue planning. Returns 202 with an import_id; poll the status
    endpoint until AWAITING_CONFIRMATION, review the plan, then apply.

    Args:
        pod_id (UUID):
        body (BodyPodBundleImportStart):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ImportStatusResponse]
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
    body: BodyPodBundleImportStart,
) -> ErrorResponse | ImportStatusResponse | None:
    """Start Pod Import

     Upload a pod bundle (.zip) and enqueue planning. Returns 202 with an import_id; poll the status
    endpoint until AWAITING_CONFIRMATION, review the plan, then apply.

    Args:
        pod_id (UUID):
        body (BodyPodBundleImportStart):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ImportStatusResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

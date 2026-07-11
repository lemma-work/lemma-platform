from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.app_bundle_upload_request import AppBundleUploadRequest
from ...models.app_bundle_upload_response import AppBundleUploadResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    app_name: str,
    *,
    body: AppBundleUploadRequest | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/apps/{app_name}/bundle".format(
            pod_id=quote(str(pod_id), safe=""),
            app_name=quote(str(app_name), safe=""),
        ),
    }

    if not isinstance(body, Unset):
        _kwargs["files"] = body.to_multipart()

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AppBundleUploadResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AppBundleUploadResponse.from_dict(response.json())

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
) -> Response[AppBundleUploadResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppBundleUploadRequest | Unset = UNSET,
) -> Response[AppBundleUploadResponse | ErrorResponse]:
    """Upload App Bundle

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppBundleUploadRequest | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppBundleUploadResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppBundleUploadRequest | Unset = UNSET,
) -> AppBundleUploadResponse | ErrorResponse | None:
    """Upload App Bundle

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppBundleUploadRequest | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppBundleUploadResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        app_name=app_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppBundleUploadRequest | Unset = UNSET,
) -> Response[AppBundleUploadResponse | ErrorResponse]:
    """Upload App Bundle

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppBundleUploadRequest | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AppBundleUploadResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        app_name=app_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    app_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: AppBundleUploadRequest | Unset = UNSET,
) -> AppBundleUploadResponse | ErrorResponse | None:
    """Upload App Bundle

    Args:
        pod_id (UUID):
        app_name (str):
        body (AppBundleUploadRequest | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AppBundleUploadResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            app_name=app_name,
            client=client,
            body=body,
        )
    ).parsed

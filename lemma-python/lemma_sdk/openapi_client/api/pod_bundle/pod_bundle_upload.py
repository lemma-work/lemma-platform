from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.fastapi_compat_v2_body_pod_bundle_upload import (
    FastapiCompatV2BodyPodBundleUpload,
)
from ...models.upload_response import UploadResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: FastapiCompatV2BodyPodBundleUpload,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/bundle/uploads".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["files"] = body.to_multipart()

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | UploadResponse | None:
    if response.status_code == 201:
        response_201 = UploadResponse.from_dict(response.json())

        return response_201

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | UploadResponse]:
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
    body: FastapiCompatV2BodyPodBundleUpload,
) -> Response[ErrorResponse | UploadResponse]:
    """Stage A Local Bundle Upload

     Upload a local .zip bundle and receive a signed lemma download URL to pass to POST …/bundle/imports
    as kind=URL. The only multipart endpoint; it stages bytes and mints a URL, nothing more.

    Args:
        pod_id (UUID):
        body (FastapiCompatV2BodyPodBundleUpload):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UploadResponse]
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
    body: FastapiCompatV2BodyPodBundleUpload,
) -> ErrorResponse | UploadResponse | None:
    """Stage A Local Bundle Upload

     Upload a local .zip bundle and receive a signed lemma download URL to pass to POST …/bundle/imports
    as kind=URL. The only multipart endpoint; it stages bytes and mints a URL, nothing more.

    Args:
        pod_id (UUID):
        body (FastapiCompatV2BodyPodBundleUpload):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UploadResponse
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
    body: FastapiCompatV2BodyPodBundleUpload,
) -> Response[ErrorResponse | UploadResponse]:
    """Stage A Local Bundle Upload

     Upload a local .zip bundle and receive a signed lemma download URL to pass to POST …/bundle/imports
    as kind=URL. The only multipart endpoint; it stages bytes and mints a URL, nothing more.

    Args:
        pod_id (UUID):
        body (FastapiCompatV2BodyPodBundleUpload):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | UploadResponse]
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
    body: FastapiCompatV2BodyPodBundleUpload,
) -> ErrorResponse | UploadResponse | None:
    """Stage A Local Bundle Upload

     Upload a local .zip bundle and receive a signed lemma download URL to pass to POST …/bundle/imports
    as kind=URL. The only multipart endpoint; it stages bytes and mints a URL, nothing more.

    Args:
        pod_id (UUID):
        body (FastapiCompatV2BodyPodBundleUpload):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | UploadResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

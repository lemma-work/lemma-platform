from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.signed_url_revoke_response import SignedUrlRevokeResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    code: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/pods/{pod_id}/datastore/files/signed-urls/{code}".format(
            pod_id=quote(str(pod_id), safe=""),
            code=quote(str(code), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SignedUrlRevokeResponse | None:
    if response.status_code == 200:
        response_200 = SignedUrlRevokeResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SignedUrlRevokeResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    code: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SignedUrlRevokeResponse]:
    """Revoke a public signed URL

     Kill a link now rather than waiting out its expiry.

    Answers 200 either way: a code that is already dead, or was never this
    pod's, is reported as ``revoked: false`` rather than 404, so that a caller
    cleaning up cannot use this endpoint to discover which codes exist.

    Args:
        pod_id (UUID):
        code (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignedUrlRevokeResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        code=code,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    code: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SignedUrlRevokeResponse | None:
    """Revoke a public signed URL

     Kill a link now rather than waiting out its expiry.

    Answers 200 either way: a code that is already dead, or was never this
    pod's, is reported as ``revoked: false`` rather than 404, so that a caller
    cleaning up cannot use this endpoint to discover which codes exist.

    Args:
        pod_id (UUID):
        code (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignedUrlRevokeResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        code=code,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    code: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SignedUrlRevokeResponse]:
    """Revoke a public signed URL

     Kill a link now rather than waiting out its expiry.

    Answers 200 either way: a code that is already dead, or was never this
    pod's, is reported as ``revoked: false`` rather than 404, so that a caller
    cleaning up cannot use this endpoint to discover which codes exist.

    Args:
        pod_id (UUID):
        code (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SignedUrlRevokeResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        code=code,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    code: str,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | SignedUrlRevokeResponse | None:
    """Revoke a public signed URL

     Kill a link now rather than waiting out its expiry.

    Answers 200 either way: a code that is already dead, or was never this
    pod's, is reported as ``revoked: false`` rather than 404, so that a caller
    cleaning up cannot use this endpoint to discover which codes exist.

    Args:
        pod_id (UUID):
        code (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SignedUrlRevokeResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            code=code,
            client=client,
        )
    ).parsed

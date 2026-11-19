from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.preview_response import PreviewResponse
from ...types import Response


def _get_kwargs(
    ref: str,
    preview_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/machines/{ref}/previews/{preview_id}".format(
            ref=quote(str(ref), safe=""),
            preview_id=quote(str(preview_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PreviewResponse | None:
    if response.status_code == 200:
        response_200 = PreviewResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PreviewResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ref: str,
    preview_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PreviewResponse]:
    """Revoke a preview; its URL stops answering

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        preview_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PreviewResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        preview_id=preview_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    preview_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PreviewResponse | None:
    """Revoke a preview; its URL stops answering

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        preview_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PreviewResponse
    """

    return sync_detailed(
        ref=ref,
        preview_id=preview_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    ref: str,
    preview_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PreviewResponse]:
    """Revoke a preview; its URL stops answering

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        preview_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PreviewResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        preview_id=preview_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    preview_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PreviewResponse | None:
    """Revoke a preview; its URL stops answering

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        preview_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PreviewResponse
    """

    return (
        await asyncio_detailed(
            ref=ref,
            preview_id=preview_id,
            client=client,
        )
    ).parsed

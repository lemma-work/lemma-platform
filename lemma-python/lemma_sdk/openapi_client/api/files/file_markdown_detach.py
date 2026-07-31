from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.file_detail_response import FileDetailResponse
from ...types import UNSET, Response


def _get_kwargs(
    pod_id: UUID,
    *,
    path: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["path"] = path

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/pods/{pod_id}/datastore/files/by-path/markdown".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FileDetailResponse | None:
    if response.status_code == 200:
        response_200 = FileDetailResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FileDetailResponse]:
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
    path: str,
) -> Response[ErrorResponse | FileDetailResponse]:
    """Detach Document Markdown

     Remove a document's user-provided markdown so it reverts to extraction.

    Args:
        pod_id (UUID):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        path=path,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    path: str,
) -> ErrorResponse | FileDetailResponse | None:
    """Detach Document Markdown

     Remove a document's user-provided markdown so it reverts to extraction.

    Args:
        pod_id (UUID):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileDetailResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        path=path,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    path: str,
) -> Response[ErrorResponse | FileDetailResponse]:
    """Detach Document Markdown

     Remove a document's user-provided markdown so it reverts to extraction.

    Args:
        pod_id (UUID):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        path=path,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    path: str,
) -> ErrorResponse | FileDetailResponse | None:
    """Detach Document Markdown

     Remove a document's user-provided markdown so it reverts to extraction.

    Args:
        pod_id (UUID):
        path (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileDetailResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            path=path,
        )
    ).parsed

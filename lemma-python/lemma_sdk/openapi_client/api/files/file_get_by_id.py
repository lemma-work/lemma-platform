from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.file_detail_response import FileDetailResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    file_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/datastore/files/{file_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            file_id=quote(str(file_id), safe=""),
        ),
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
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | FileDetailResponse]:
    r"""Get File by ID

     Read one file by its id.

    Files were addressable only by path, which forced share links to carry one —
    and a personal path is the alias ``/me``, resolved against *whoever is
    asking*. A link to ``/me/notes.md`` therefore pointed at the recipient's own
    file: a 404 that reads as \"deleted\", or, on a name collision, silently the
    wrong document. An id means the same file for everyone, and survives renames
    and moves besides.

    Args:
        pod_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        file_id=file_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | FileDetailResponse | None:
    r"""Get File by ID

     Read one file by its id.

    Files were addressable only by path, which forced share links to carry one —
    and a personal path is the alias ``/me``, resolved against *whoever is
    asking*. A link to ``/me/notes.md`` therefore pointed at the recipient's own
    file: a 404 that reads as \"deleted\", or, on a name collision, silently the
    wrong document. An id means the same file for everyone, and survives renames
    and moves besides.

    Args:
        pod_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileDetailResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        file_id=file_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | FileDetailResponse]:
    r"""Get File by ID

     Read one file by its id.

    Files were addressable only by path, which forced share links to carry one —
    and a personal path is the alias ``/me``, resolved against *whoever is
    asking*. A link to ``/me/notes.md`` therefore pointed at the recipient's own
    file: a 404 that reads as \"deleted\", or, on a name collision, silently the
    wrong document. An id means the same file for everyone, and survives renames
    and moves besides.

    Args:
        pod_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FileDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        file_id=file_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | FileDetailResponse | None:
    r"""Get File by ID

     Read one file by its id.

    Files were addressable only by path, which forced share links to carry one —
    and a personal path is the alias ``/me``, resolved against *whoever is
    asking*. A link to ``/me/notes.md`` therefore pointed at the recipient's own
    file: a 404 that reads as \"deleted\", or, on a name collision, silently the
    wrong document. An id means the same file for everyone, and survives renames
    and moves besides.

    Args:
        pod_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FileDetailResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            file_id=file_id,
            client=client,
        )
    ).parsed

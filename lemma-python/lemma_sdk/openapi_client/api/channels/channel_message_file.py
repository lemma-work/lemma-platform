from http import HTTPStatus
from io import BytesIO
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...types import File, Response


def _get_kwargs(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/channels/{channel_id}/messages/{message_id}/files/{file_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            channel_id=quote(str(channel_id), safe=""),
            message_id=quote(str(message_id), safe=""),
            file_id=quote(str(file_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | File | None:
    if response.status_code == 200:
        response_200 = File(payload=BytesIO(response.content))

        return response_200

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ErrorResponse | File]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | File]:
    """Line File

     A file a line shares, for a reader of the line: read as its writer
    reads it. Gone with the line, or once its writer can no longer read it.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | File]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        file_id=file_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | File | None:
    """Line File

     A file a line shares, for a reader of the line: read as its writer
    reads it. Gone with the line, or once its writer can no longer read it.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | File
    """

    return sync_detailed(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        file_id=file_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | File]:
    """Line File

     A file a line shares, for a reader of the line: read as its writer
    reads it. Gone with the line, or once its writer can no longer read it.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | File]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        channel_id=channel_id,
        message_id=message_id,
        file_id=file_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    file_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | File | None:
    """Line File

     A file a line shares, for a reader of the line: read as its writer
    reads it. Gone with the line, or once its writer can no longer read it.

    Args:
        pod_id (UUID):
        channel_id (UUID):
        message_id (UUID):
        file_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | File
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            channel_id=channel_id,
            message_id=message_id,
            file_id=file_id,
            client=client,
        )
    ).parsed

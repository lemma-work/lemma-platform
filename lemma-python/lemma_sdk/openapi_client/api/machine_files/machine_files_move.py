from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.machine_file_entry import MachineFileEntry
from ...models.move_request import MoveRequest
from ...types import Response


def _get_kwargs(
    ref: str,
    *,
    body: MoveRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/machines/{ref}/files:move".format(
            ref=quote(str(ref), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MachineFileEntry | None:
    if response.status_code == 200:
        response_200 = MachineFileEntry.from_dict(response.json())

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
) -> Response[ErrorResponse | MachineFileEntry]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: MoveRequest,
) -> Response[ErrorResponse | MachineFileEntry]:
    """Move or rename a path on a machine

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (MoveRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MachineFileEntry]
    """

    kwargs = _get_kwargs(
        ref=ref,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: MoveRequest,
) -> ErrorResponse | MachineFileEntry | None:
    """Move or rename a path on a machine

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (MoveRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MachineFileEntry
    """

    return sync_detailed(
        ref=ref,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: MoveRequest,
) -> Response[ErrorResponse | MachineFileEntry]:
    """Move or rename a path on a machine

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (MoveRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MachineFileEntry]
    """

    kwargs = _get_kwargs(
        ref=ref,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    *,
    client: AuthenticatedClient | Client,
    body: MoveRequest,
) -> ErrorResponse | MachineFileEntry | None:
    """Move or rename a path on a machine

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        body (MoveRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MachineFileEntry
    """

    return (
        await asyncio_detailed(
            ref=ref,
            client=client,
            body=body,
        )
    ).parsed

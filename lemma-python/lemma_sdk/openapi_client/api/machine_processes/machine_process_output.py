from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.process_output_response import ProcessOutputResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ref: str,
    process_id: UUID,
    *,
    after: int | Unset = 0,
    wait_seconds: float | Unset = 0.0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["after"] = after

    params["wait_seconds"] = wait_seconds

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/machines/{ref}/processes/{process_id}/output".format(
            ref=quote(str(ref), safe=""),
            process_id=quote(str(process_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ProcessOutputResponse | None:
    if response.status_code == 200:
        response_200 = ProcessOutputResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ProcessOutputResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    ref: str,
    process_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    wait_seconds: float | Unset = 0.0,
) -> Response[ErrorResponse | ProcessOutputResponse]:
    """A page of a process's output

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        process_id (UUID):
        after (int | Unset): Return output after this sequence. Default: 0.
        wait_seconds (float | Unset): Wait this long for new output. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ProcessOutputResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        process_id=process_id,
        after=after,
        wait_seconds=wait_seconds,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ref: str,
    process_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    wait_seconds: float | Unset = 0.0,
) -> ErrorResponse | ProcessOutputResponse | None:
    """A page of a process's output

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        process_id (UUID):
        after (int | Unset): Return output after this sequence. Default: 0.
        wait_seconds (float | Unset): Wait this long for new output. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ProcessOutputResponse
    """

    return sync_detailed(
        ref=ref,
        process_id=process_id,
        client=client,
        after=after,
        wait_seconds=wait_seconds,
    ).parsed


async def asyncio_detailed(
    ref: str,
    process_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    wait_seconds: float | Unset = 0.0,
) -> Response[ErrorResponse | ProcessOutputResponse]:
    """A page of a process's output

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        process_id (UUID):
        after (int | Unset): Return output after this sequence. Default: 0.
        wait_seconds (float | Unset): Wait this long for new output. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ProcessOutputResponse]
    """

    kwargs = _get_kwargs(
        ref=ref,
        process_id=process_id,
        after=after,
        wait_seconds=wait_seconds,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ref: str,
    process_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    after: int | Unset = 0,
    wait_seconds: float | Unset = 0.0,
) -> ErrorResponse | ProcessOutputResponse | None:
    """A page of a process's output

    Args:
        ref (str): Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the pod in
            context), `mine:<name>`, or its id.
        process_id (UUID):
        after (int | Unset): Return output after this sequence. Default: 0.
        wait_seconds (float | Unset): Wait this long for new output. Default: 0.0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ProcessOutputResponse
    """

    return (
        await asyncio_detailed(
            ref=ref,
            process_id=process_id,
            client=client,
            after=after,
            wait_seconds=wait_seconds,
        )
    ).parsed

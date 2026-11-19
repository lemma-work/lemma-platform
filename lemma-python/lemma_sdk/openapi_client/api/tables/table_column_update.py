from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.table_detail_response import TableDetailResponse
from ...models.update_column_request import UpdateColumnRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    *,
    body: UpdateColumnRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/datastore/tables/{table_name}/columns/{column_name}".format(
            pod_id=quote(str(pod_id), safe=""),
            table_name=quote(str(table_name), safe=""),
            column_name=quote(str(column_name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | TableDetailResponse | None:
    if response.status_code == 200:
        response_200 = TableDetailResponse.from_dict(response.json())

        return response_200

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
) -> Response[ErrorResponse | TableDetailResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateColumnRequest,
) -> Response[ErrorResponse | TableDetailResponse]:
    """Update Column

     Add options to an ENUM column, drop a column's foreign key, or both. `options` must include every
    option the column has now: removing one is refused (422), because rows may already hold it, and
    sending `options` for a column that is not ENUM is refused too. `drop_foreign_key` works on any
    column. Sending what the column already is changes nothing.

    Args:
        pod_id (UUID):
        table_name (str):
        column_name (str):
        body (UpdateColumnRequest): Schema for changing an existing column.

            An ENUM column's options can be added to, and a column's foreign key
            dropped; nothing else changes. Unknown fields are refused rather than
            ignored, so a caller asking for a change this does not make hears so
            instead of getting a 200 for nothing.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        column_name=column_name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateColumnRequest,
) -> ErrorResponse | TableDetailResponse | None:
    """Update Column

     Add options to an ENUM column, drop a column's foreign key, or both. `options` must include every
    option the column has now: removing one is refused (422), because rows may already hold it, and
    sending `options` for a column that is not ENUM is refused too. `drop_foreign_key` works on any
    column. Sending what the column already is changes nothing.

    Args:
        pod_id (UUID):
        table_name (str):
        column_name (str):
        body (UpdateColumnRequest): Schema for changing an existing column.

            An ENUM column's options can be added to, and a column's foreign key
            dropped; nothing else changes. Unknown fields are refused rather than
            ignored, so a caller asking for a change this does not make hears so
            instead of getting a 200 for nothing.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableDetailResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        table_name=table_name,
        column_name=column_name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateColumnRequest,
) -> Response[ErrorResponse | TableDetailResponse]:
    """Update Column

     Add options to an ENUM column, drop a column's foreign key, or both. `options` must include every
    option the column has now: removing one is refused (422), because rows may already hold it, and
    sending `options` for a column that is not ENUM is refused too. `drop_foreign_key` works on any
    column. Sending what the column already is changes nothing.

    Args:
        pod_id (UUID):
        table_name (str):
        column_name (str):
        body (UpdateColumnRequest): Schema for changing an existing column.

            An ENUM column's options can be added to, and a column's foreign key
            dropped; nothing else changes. Unknown fields are refused rather than
            ignored, so a caller asking for a change this does not make hears so
            instead of getting a 200 for nothing.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | TableDetailResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        column_name=column_name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    table_name: str,
    column_name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateColumnRequest,
) -> ErrorResponse | TableDetailResponse | None:
    """Update Column

     Add options to an ENUM column, drop a column's foreign key, or both. `options` must include every
    option the column has now: removing one is refused (422), because rows may already hold it, and
    sending `options` for a column that is not ENUM is refused too. `drop_foreign_key` works on any
    column. Sending what the column already is changes nothing.

    Args:
        pod_id (UUID):
        table_name (str):
        column_name (str):
        body (UpdateColumnRequest): Schema for changing an existing column.

            An ENUM column's options can be added to, and a column's foreign key
            dropped; nothing else changes. Unknown fields are refused rather than
            ignored, so a caller asking for a change this does not make hears so
            instead of getting a 200 for nothing.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | TableDetailResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            table_name=table_name,
            column_name=column_name,
            client=client,
            body=body,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.record_access_mode import RecordAccessMode
from ...models.record_update_response_record_update import (
    RecordUpdateResponseRecordUpdate,
)
from ...models.update_record_request import UpdateRecordRequest
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    *,
    body: UpdateRecordRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    json_mode: None | str | Unset
    if isinstance(mode, Unset):
        json_mode = UNSET
    elif isinstance(mode, RecordAccessMode):
        json_mode = mode.value
    else:
        json_mode = mode
    params["mode"] = json_mode

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/datastore/tables/{table_name}/records/{record_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            table_name=quote(str(table_name), safe=""),
            record_id=quote(str(record_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | RecordUpdateResponseRecordUpdate | None:
    if response.status_code == 200:
        response_200 = RecordUpdateResponseRecordUpdate.from_dict(response.json())

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
) -> Response[ErrorResponse | RecordUpdateResponseRecordUpdate]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRecordRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[ErrorResponse | RecordUpdateResponseRecordUpdate]:
    """Update Record

     Patch a record by primary key. Returns the updated record object (no envelope). Pass
    `expected_updated_at` to make the patch conditional on the row not having changed since it was read;
    the request then answers 409 rather than overwriting another client's edit.

    Args:
        pod_id (UUID):
        table_name (str):
        record_id (str):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.
        body (UpdateRecordRequest): Schema for updating a record.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RecordUpdateResponseRecordUpdate]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        record_id=record_id,
        body=body,
        mode=mode,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRecordRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> ErrorResponse | RecordUpdateResponseRecordUpdate | None:
    """Update Record

     Patch a record by primary key. Returns the updated record object (no envelope). Pass
    `expected_updated_at` to make the patch conditional on the row not having changed since it was read;
    the request then answers 409 rather than overwriting another client's edit.

    Args:
        pod_id (UUID):
        table_name (str):
        record_id (str):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.
        body (UpdateRecordRequest): Schema for updating a record.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RecordUpdateResponseRecordUpdate
    """

    return sync_detailed(
        pod_id=pod_id,
        table_name=table_name,
        record_id=record_id,
        client=client,
        body=body,
        mode=mode,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRecordRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[ErrorResponse | RecordUpdateResponseRecordUpdate]:
    """Update Record

     Patch a record by primary key. Returns the updated record object (no envelope). Pass
    `expected_updated_at` to make the patch conditional on the row not having changed since it was read;
    the request then answers 409 rather than overwriting another client's edit.

    Args:
        pod_id (UUID):
        table_name (str):
        record_id (str):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.
        body (UpdateRecordRequest): Schema for updating a record.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RecordUpdateResponseRecordUpdate]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        record_id=record_id,
        body=body,
        mode=mode,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    table_name: str,
    record_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateRecordRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> ErrorResponse | RecordUpdateResponseRecordUpdate | None:
    """Update Record

     Patch a record by primary key. Returns the updated record object (no envelope). Pass
    `expected_updated_at` to make the patch conditional on the row not having changed since it was read;
    the request then answers 409 rather than overwriting another client's edit.

    Args:
        pod_id (UUID):
        table_name (str):
        record_id (str):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.
        body (UpdateRecordRequest): Schema for updating a record.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RecordUpdateResponseRecordUpdate
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            table_name=table_name,
            record_id=record_id,
            client=client,
            body=body,
            mode=mode,
        )
    ).parsed

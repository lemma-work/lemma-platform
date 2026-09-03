from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.datastore_query_request import DatastoreQueryRequest
from ...models.datastore_query_response import DatastoreQueryResponse
from ...models.error_response import ErrorResponse
from ...models.record_access_mode import RecordAccessMode
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    body: DatastoreQueryRequest,
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
        "method": "post",
        "url": "/pods/{pod_id}/datastore/query".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> DatastoreQueryResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = DatastoreQueryResponse.from_dict(response.json())

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
) -> Response[DatastoreQueryResponse | ErrorResponse]:
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
    body: DatastoreQueryRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[DatastoreQueryResponse | ErrorResponse]:
    """Execute Query

     Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-
    table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the
    caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which
    requires permission to administer each referenced RLS table. Only a single read-only statement is
    permitted; mutating statements and cross-schema references are rejected. Results are capped at the
    deployment's row limit: `total` is how many rows came back, and `truncated` says whether more
    matched.

    Args:
        pod_id (UUID):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables
            referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user
            — the per-user data apps and functions expect. `ADMIN` returns every member's rows and
            requires permission to administer every RLS table the query touches; a caller without it
            gets a 403. Non-RLS tables are unaffected.
        body (DatastoreQueryRequest): Schema for executing read-only SQL within a datastore.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DatastoreQueryResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        mode=mode,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: DatastoreQueryRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> DatastoreQueryResponse | ErrorResponse | None:
    """Execute Query

     Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-
    table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the
    caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which
    requires permission to administer each referenced RLS table. Only a single read-only statement is
    permitted; mutating statements and cross-schema references are rejected. Results are capped at the
    deployment's row limit: `total` is how many rows came back, and `truncated` says whether more
    matched.

    Args:
        pod_id (UUID):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables
            referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user
            — the per-user data apps and functions expect. `ADMIN` returns every member's rows and
            requires permission to administer every RLS table the query touches; a caller without it
            gets a 403. Non-RLS tables are unaffected.
        body (DatastoreQueryRequest): Schema for executing read-only SQL within a datastore.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DatastoreQueryResponse | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
        mode=mode,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: DatastoreQueryRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[DatastoreQueryResponse | ErrorResponse]:
    """Execute Query

     Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-
    table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the
    caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which
    requires permission to administer each referenced RLS table. Only a single read-only statement is
    permitted; mutating statements and cross-schema references are rejected. Results are capped at the
    deployment's row limit: `total` is how many rows came back, and `truncated` says whether more
    matched.

    Args:
        pod_id (UUID):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables
            referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user
            — the per-user data apps and functions expect. `ADMIN` returns every member's rows and
            requires permission to administer every RLS table the query touches; a caller without it
            gets a 403. Non-RLS tables are unaffected.
        body (DatastoreQueryRequest): Schema for executing read-only SQL within a datastore.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[DatastoreQueryResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
        mode=mode,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: DatastoreQueryRequest,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> DatastoreQueryResponse | ErrorResponse | None:
    """Execute Query

     Execute a read-only SQL query inside the datastore schema. Joins, aggregates, subqueries, and cross-
    table reads are allowed, including across RLS-enabled tables — rows of RLS tables are scoped to the
    caller by default (pod admins included). Pass `mode=admin` to read every member's rows, which
    requires permission to administer each referenced RLS table. Only a single read-only statement is
    permitted; mutating statements and cross-schema references are rejected. Results are capped at the
    deployment's row limit: `total` is how many rows came back, and `truncated` says whether more
    matched.

    Args:
        pod_id (UUID):
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables
            referenced by the query. Omitted/`USER` (default) scopes their rows to the signed-in user
            — the per-user data apps and functions expect. `ADMIN` returns every member's rows and
            requires permission to administer every RLS table the query touches; a caller without it
            gets a 403. Non-RLS tables are unaffected.
        body (DatastoreQueryRequest): Schema for executing read-only SQL within a datastore.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        DatastoreQueryResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
            mode=mode,
        )
    ).parsed

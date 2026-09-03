from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.record_access_mode import RecordAccessMode
from ...models.record_list_response import RecordListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    table_name: str,
    *,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    filter_: list[str] | None | Unset = UNSET,
    sort: list[str] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    params["offset"] = offset

    json_filter_: list[str] | None | Unset
    if isinstance(filter_, Unset):
        json_filter_ = UNSET
    elif isinstance(filter_, list):
        json_filter_ = filter_

    else:
        json_filter_ = filter_
    params["filter"] = json_filter_

    json_sort: list[str] | None | Unset
    if isinstance(sort, Unset):
        json_sort = UNSET
    elif isinstance(sort, list):
        json_sort = sort

    else:
        json_sort = sort
    params["sort"] = json_sort

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

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
        "method": "get",
        "url": "/pods/{pod_id}/datastore/tables/{table_name}/records".format(
            pod_id=quote(str(pod_id), safe=""),
            table_name=quote(str(table_name), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | RecordListResponse | None:
    if response.status_code == 200:
        response_200 = RecordListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | RecordListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    filter_: list[str] | None | Unset = UNSET,
    sort: list[str] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[ErrorResponse | RecordListResponse]:
    """List Records

     List table records with token pagination only. Use the datastore query endpoint for joins,
    aggregates, or custom read-only SQL.

    Args:
        pod_id (UUID):
        table_name (str):
        limit (int | Unset): Max number of rows to return, up to 1000. Page beyond that with
            `page_token`. Default: 20.
        offset (int | Unset): Row offset for direct pagination. Default: 0.
        filter_ (list[str] | None | Unset): Optional repeated JSON filters for advanced
            comparisons. Each `filter` value must be a JSON object with shape
            `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed
            operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. Repeat the query
            parameter to combine multiple filters with AND semantics. Examples:
            `filter={"field":"amount","op":"gt","value":100}` and
            `filter={"field":"status","op":"eq","value":"OPEN"}`.
        sort (list[str] | None | Unset): Optional repeated JSON sort clauses. Each `sort` value
            must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`.
            Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column
            sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
        page_token (None | str | Unset): Opaque token from a previous response page.
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RecordListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        limit=limit,
        offset=offset,
        filter_=filter_,
        sort=sort,
        page_token=page_token,
        mode=mode,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    filter_: list[str] | None | Unset = UNSET,
    sort: list[str] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> ErrorResponse | RecordListResponse | None:
    """List Records

     List table records with token pagination only. Use the datastore query endpoint for joins,
    aggregates, or custom read-only SQL.

    Args:
        pod_id (UUID):
        table_name (str):
        limit (int | Unset): Max number of rows to return, up to 1000. Page beyond that with
            `page_token`. Default: 20.
        offset (int | Unset): Row offset for direct pagination. Default: 0.
        filter_ (list[str] | None | Unset): Optional repeated JSON filters for advanced
            comparisons. Each `filter` value must be a JSON object with shape
            `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed
            operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. Repeat the query
            parameter to combine multiple filters with AND semantics. Examples:
            `filter={"field":"amount","op":"gt","value":100}` and
            `filter={"field":"status","op":"eq","value":"OPEN"}`.
        sort (list[str] | None | Unset): Optional repeated JSON sort clauses. Each `sort` value
            must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`.
            Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column
            sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
        page_token (None | str | Unset): Opaque token from a previous response page.
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RecordListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        table_name=table_name,
        client=client,
        limit=limit,
        offset=offset,
        filter_=filter_,
        sort=sort,
        page_token=page_token,
        mode=mode,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    filter_: list[str] | None | Unset = UNSET,
    sort: list[str] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> Response[ErrorResponse | RecordListResponse]:
    """List Records

     List table records with token pagination only. Use the datastore query endpoint for joins,
    aggregates, or custom read-only SQL.

    Args:
        pod_id (UUID):
        table_name (str):
        limit (int | Unset): Max number of rows to return, up to 1000. Page beyond that with
            `page_token`. Default: 20.
        offset (int | Unset): Row offset for direct pagination. Default: 0.
        filter_ (list[str] | None | Unset): Optional repeated JSON filters for advanced
            comparisons. Each `filter` value must be a JSON object with shape
            `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed
            operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. Repeat the query
            parameter to combine multiple filters with AND semantics. Examples:
            `filter={"field":"amount","op":"gt","value":100}` and
            `filter={"field":"status","op":"eq","value":"OPEN"}`.
        sort (list[str] | None | Unset): Optional repeated JSON sort clauses. Each `sort` value
            must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`.
            Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column
            sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
        page_token (None | str | Unset): Opaque token from a previous response page.
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | RecordListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        table_name=table_name,
        limit=limit,
        offset=offset,
        filter_=filter_,
        sort=sort,
        page_token=page_token,
        mode=mode,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    table_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
    filter_: list[str] | None | Unset = UNSET,
    sort: list[str] | None | Unset = UNSET,
    page_token: None | str | Unset = UNSET,
    mode: None | RecordAccessMode | Unset = UNSET,
) -> ErrorResponse | RecordListResponse | None:
    """List Records

     List table records with token pagination only. Use the datastore query endpoint for joins,
    aggregates, or custom read-only SQL.

    Args:
        pod_id (UUID):
        table_name (str):
        limit (int | Unset): Max number of rows to return, up to 1000. Page beyond that with
            `page_token`. Default: 20.
        offset (int | Unset): Row offset for direct pagination. Default: 0.
        filter_ (list[str] | None | Unset): Optional repeated JSON filters for advanced
            comparisons. Each `filter` value must be a JSON object with shape
            `{"field":"<column_name>","op":"<operator>","value":<comparison_value>}`. Allowed
            operators are: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `like`, `ilike`. Repeat the query
            parameter to combine multiple filters with AND semantics. Examples:
            `filter={"field":"amount","op":"gt","value":100}` and
            `filter={"field":"status","op":"eq","value":"OPEN"}`.
        sort (list[str] | None | Unset): Optional repeated JSON sort clauses. Each `sort` value
            must be a JSON object with shape `{"field":"<column_name>","direction":"<direction>"}`.
            Allowed directions are: `asc`, `desc`. Repeat the query parameter to provide multi-column
            sorting in priority order. Example: `sort={"field":"created_at","direction":"desc"}`.
        page_token (None | str | Unset): Opaque token from a previous response page.
        mode (None | RecordAccessMode | Unset): Row-visibility mode for RLS-enabled tables.
            Omitted/`USER` (default) scopes rows to the signed-in user's own records — the per-user
            semantics an app app expects. `ADMIN` returns/operates on every member's rows and requires
            permission to administer the table; a caller without it gets a 403. Ignored for non-RLS
            tables, whose rows are shared by all members.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | RecordListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            table_name=table_name,
            client=client,
            limit=limit,
            offset=offset,
            filter_=filter_,
            sort=sort,
            page_token=page_token,
            mode=mode,
        )
    ).parsed

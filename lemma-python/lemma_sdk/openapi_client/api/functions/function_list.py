from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.function_list_response import FunctionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    include: list[str] | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    json_include: list[str] | Unset = UNSET
    if not isinstance(include, Unset):
        json_include = include

    params["include"] = json_include

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/functions".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FunctionListResponse | None:
    if response.status_code == 200:
        response_200 = FunctionListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FunctionListResponse]:
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
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    include: list[str] | Unset = UNSET,
) -> Response[ErrorResponse | FunctionListResponse]:
    """List Functions

     List all functions in a pod

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each function's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-function permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        page_token=page_token,
        include=include,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    include: list[str] | Unset = UNSET,
) -> ErrorResponse | FunctionListResponse | None:
    """List Functions

     List all functions in a pod

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each function's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-function permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        limit=limit,
        page_token=page_token,
        include=include,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    include: list[str] | Unset = UNSET,
) -> Response[ErrorResponse | FunctionListResponse]:
    """List Functions

     List all functions in a pod

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each function's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-function permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        limit=limit,
        page_token=page_token,
        include=include,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 100,
    page_token: None | str | Unset = UNSET,
    include: list[str] | Unset = UNSET,
) -> ErrorResponse | FunctionListResponse | None:
    """List Functions

     List all functions in a pod

    Args:
        pod_id (UUID):
        limit (int | Unset):  Default: 100.
        page_token (None | str | Unset):
        include (list[str] | Unset): Extra data to embed. `permissions` attaches each function's
            resource grants, resolved for the whole page in one query — without it, a caller that
            needs grants must call the per-function permissions endpoint once per row.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            limit=limit,
            page_token=page_token,
            include=include,
        )
    ).parsed

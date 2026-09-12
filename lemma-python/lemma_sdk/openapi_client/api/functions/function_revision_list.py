from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.function_revision_list_response import FunctionRevisionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    function_name: str,
    *,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["limit"] = limit

    json_page_token: None | str | Unset
    if isinstance(page_token, Unset):
        json_page_token = UNSET
    else:
        json_page_token = page_token
    params["page_token"] = json_page_token

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/functions/{function_name}/revisions".format(
            pod_id=quote(str(pod_id), safe=""),
            function_name=quote(str(function_name), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FunctionRevisionListResponse | None:
    if response.status_code == 200:
        response_200 = FunctionRevisionListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FunctionRevisionListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | FunctionRevisionListResponse]:
    """List Function Revisions

     List the built revisions of a function, newest first.

    Args:
        pod_id (UUID):
        function_name (str):
        limit (int | Unset): Max revisions to return, up to 200. Page beyond that with
            `page_token`. Default: 50.
        page_token (None | str | Unset): `next_page_token` from the previous page.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionRevisionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        limit=limit,
        page_token=page_token,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | FunctionRevisionListResponse | None:
    """List Function Revisions

     List the built revisions of a function, newest first.

    Args:
        pod_id (UUID):
        function_name (str):
        limit (int | Unset): Max revisions to return, up to 200. Page beyond that with
            `page_token`. Default: 50.
        page_token (None | str | Unset): `next_page_token` from the previous page.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionRevisionListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        function_name=function_name,
        client=client,
        limit=limit,
        page_token=page_token,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> Response[ErrorResponse | FunctionRevisionListResponse]:
    """List Function Revisions

     List the built revisions of a function, newest first.

    Args:
        pod_id (UUID):
        function_name (str):
        limit (int | Unset): Max revisions to return, up to 200. Page beyond that with
            `page_token`. Default: 50.
        page_token (None | str | Unset): `next_page_token` from the previous page.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FunctionRevisionListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        function_name=function_name,
        limit=limit,
        page_token=page_token,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    function_name: str,
    *,
    client: AuthenticatedClient | Client,
    limit: int | Unset = 50,
    page_token: None | str | Unset = UNSET,
) -> ErrorResponse | FunctionRevisionListResponse | None:
    """List Function Revisions

     List the built revisions of a function, newest first.

    Args:
        pod_id (UUID):
        function_name (str):
        limit (int | Unset): Max revisions to return, up to 200. Page beyond that with
            `page_token`. Default: 50.
        page_token (None | str | Unset): `next_page_token` from the previous page.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FunctionRevisionListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            function_name=function_name,
            client=client,
            limit=limit,
            page_token=page_token,
        )
    ).parsed

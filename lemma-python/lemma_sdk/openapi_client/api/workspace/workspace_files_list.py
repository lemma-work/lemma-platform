from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.workspace_file_list_response import WorkspaceFileListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    path: None | str | Unset = UNSET,
    wake: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_path: None | str | Unset
    if isinstance(path, Unset):
        json_path = UNSET
    else:
        json_path = path
    params["path"] = json_path

    params["wake"] = wake

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/workspace/files",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | WorkspaceFileListResponse | None:
    if response.status_code == 200:
        response_200 = WorkspaceFileListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | WorkspaceFileListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    path: None | str | Unset = UNSET,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WorkspaceFileListResponse]:
    """List workspace files

    Args:
        path (None | str | Unset):
        wake (bool | Unset): Start the workspace if it is paused. Off by default. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkspaceFileListResponse]
    """

    kwargs = _get_kwargs(
        path=path,
        wake=wake,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    path: None | str | Unset = UNSET,
    wake: bool | Unset = False,
) -> ErrorResponse | WorkspaceFileListResponse | None:
    """List workspace files

    Args:
        path (None | str | Unset):
        wake (bool | Unset): Start the workspace if it is paused. Off by default. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkspaceFileListResponse
    """

    return sync_detailed(
        client=client,
        path=path,
        wake=wake,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    path: None | str | Unset = UNSET,
    wake: bool | Unset = False,
) -> Response[ErrorResponse | WorkspaceFileListResponse]:
    """List workspace files

    Args:
        path (None | str | Unset):
        wake (bool | Unset): Start the workspace if it is paused. Off by default. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | WorkspaceFileListResponse]
    """

    kwargs = _get_kwargs(
        path=path,
        wake=wake,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    path: None | str | Unset = UNSET,
    wake: bool | Unset = False,
) -> ErrorResponse | WorkspaceFileListResponse | None:
    """List workspace files

    Args:
        path (None | str | Unset):
        wake (bool | Unset): Start the workspace if it is paused. Off by default. Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | WorkspaceFileListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            path=path,
            wake=wake,
        )
    ).parsed

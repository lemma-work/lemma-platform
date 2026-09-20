from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.first_workspace_request import FirstWorkspaceRequest
from ...models.first_workspace_response import FirstWorkspaceResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: FirstWorkspaceRequest | None | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/users/me/first-workspace",
    }

    if isinstance(body, FirstWorkspaceRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | FirstWorkspaceResponse | None:
    if response.status_code == 200:
        response_200 = FirstWorkspaceResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | FirstWorkspaceResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: FirstWorkspaceRequest | None | Unset = UNSET,
) -> Response[ErrorResponse | FirstWorkspaceResponse]:
    """Ensure The Current User Has A Workspace

     Select an eligible organization and idempotently ensure the current user has a private pod and
    assistant.

    Args:
        body (FirstWorkspaceRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FirstWorkspaceResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: FirstWorkspaceRequest | None | Unset = UNSET,
) -> ErrorResponse | FirstWorkspaceResponse | None:
    """Ensure The Current User Has A Workspace

     Select an eligible organization and idempotently ensure the current user has a private pod and
    assistant.

    Args:
        body (FirstWorkspaceRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FirstWorkspaceResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: FirstWorkspaceRequest | None | Unset = UNSET,
) -> Response[ErrorResponse | FirstWorkspaceResponse]:
    """Ensure The Current User Has A Workspace

     Select an eligible organization and idempotently ensure the current user has a private pod and
    assistant.

    Args:
        body (FirstWorkspaceRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | FirstWorkspaceResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: FirstWorkspaceRequest | None | Unset = UNSET,
) -> ErrorResponse | FirstWorkspaceResponse | None:
    """Ensure The Current User Has A Workspace

     Select an eligible organization and idempotently ensure the current user has a private pod and
    assistant.

    Args:
        body (FirstWorkspaceRequest | None | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | FirstWorkspaceResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed

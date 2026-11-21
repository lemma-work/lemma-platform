from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.plugin_install_response import PluginInstallResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    install_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/bundle/installs/{install_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            install_id=quote(str(install_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PluginInstallResponse | None:
    if response.status_code == 200:
        response_200 = PluginInstallResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PluginInstallResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    install_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PluginInstallResponse]:
    """Get Plugin Install Status

     Where each plugin of an install is: its state, steps done of its plan, the step running, and why it
    stopped. Only its starter's: anyone else gets 404.

    Args:
        pod_id (UUID):
        install_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PluginInstallResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        install_id=install_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    install_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PluginInstallResponse | None:
    """Get Plugin Install Status

     Where each plugin of an install is: its state, steps done of its plan, the step running, and why it
    stopped. Only its starter's: anyone else gets 404.

    Args:
        pod_id (UUID):
        install_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PluginInstallResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        install_id=install_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    install_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PluginInstallResponse]:
    """Get Plugin Install Status

     Where each plugin of an install is: its state, steps done of its plan, the step running, and why it
    stopped. Only its starter's: anyone else gets 404.

    Args:
        pod_id (UUID):
        install_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PluginInstallResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        install_id=install_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    install_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PluginInstallResponse | None:
    """Get Plugin Install Status

     Where each plugin of an install is: its state, steps done of its plan, the step running, and why it
    stopped. Only its starter's: anyone else gets 404.

    Args:
        pod_id (UUID):
        install_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PluginInstallResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            install_id=install_id,
            client=client,
        )
    ).parsed

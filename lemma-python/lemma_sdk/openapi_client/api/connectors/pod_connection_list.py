from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.pod_connections_response import PodConnectionsResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/connections".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | PodConnectionsResponse | None:
    if response.status_code == 200:
        response_200 = PodConnectionsResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | PodConnectionsResponse]:
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
) -> Response[ErrorResponse | PodConnectionsResponse]:
    """My Connections Here

     One row per pod connector, judged for the caller: whose account a call by them runs as (`runs_as`),
    and what they can do about it (`me.standing`: ready, install, connect, use_existing, reconnect,
    waiting or not_allowed). `install` is GitHub's: the account in use reaches no GitHub App
    installation this organization holds, and `me.github` says why and who can fix it. `coverage` is
    filled for PER_MEMBER connectors when the caller may manage the pod's connectors; `github_bot` names
    the organization's GitHub App installations an APP connector can use. It declares no connector and
    adds no account. For a GitHub account the caller owns it asks GitHub where the account stands, as
    the account page does, and may bind the installation it reaches to the organization when GitHub says
    the caller administers it and they may install apps here; for anyone else's account it binds
    nothing.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectionsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PodConnectionsResponse | None:
    """My Connections Here

     One row per pod connector, judged for the caller: whose account a call by them runs as (`runs_as`),
    and what they can do about it (`me.standing`: ready, install, connect, use_existing, reconnect,
    waiting or not_allowed). `install` is GitHub's: the account in use reaches no GitHub App
    installation this organization holds, and `me.github` says why and who can fix it. `coverage` is
    filled for PER_MEMBER connectors when the caller may manage the pod's connectors; `github_bot` names
    the organization's GitHub App installations an APP connector can use. It declares no connector and
    adds no account. For a GitHub account the caller owns it asks GitHub where the account stands, as
    the account page does, and may bind the installation it reaches to the organization when GitHub says
    the caller administers it and they may install apps here; for anyone else's account it binds
    nothing.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectionsResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | PodConnectionsResponse]:
    """My Connections Here

     One row per pod connector, judged for the caller: whose account a call by them runs as (`runs_as`),
    and what they can do about it (`me.standing`: ready, install, connect, use_existing, reconnect,
    waiting or not_allowed). `install` is GitHub's: the account in use reaches no GitHub App
    installation this organization holds, and `me.github` says why and who can fix it. `coverage` is
    filled for PER_MEMBER connectors when the caller may manage the pod's connectors; `github_bot` names
    the organization's GitHub App installations an APP connector can use. It declares no connector and
    adds no account. For a GitHub account the caller owns it asks GitHub where the account stands, as
    the account page does, and may bind the installation it reaches to the organization when GitHub says
    the caller administers it and they may install apps here; for anyone else's account it binds
    nothing.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | PodConnectionsResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> ErrorResponse | PodConnectionsResponse | None:
    """My Connections Here

     One row per pod connector, judged for the caller: whose account a call by them runs as (`runs_as`),
    and what they can do about it (`me.standing`: ready, install, connect, use_existing, reconnect,
    waiting or not_allowed). `install` is GitHub's: the account in use reaches no GitHub App
    installation this organization holds, and `me.github` says why and who can fix it. `coverage` is
    filled for PER_MEMBER connectors when the caller may manage the pod's connectors; `github_bot` names
    the organization's GitHub App installations an APP connector can use. It declares no connector and
    adds no account. For a GitHub account the caller owns it asks GitHub where the account stands, as
    the account page does, and may bind the installation it reaches to the organization when GitHub says
    the caller administers it and they may install apps here; for anyone else's account it binds
    nothing.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | PodConnectionsResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
        )
    ).parsed

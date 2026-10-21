from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.slack_app_workspace_list_response import SlackAppWorkspaceListResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/surface-setup/slack/app-creation".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SlackAppWorkspaceListResponse | None:
    if response.status_code == 200:
        response_200 = SlackAppWorkspaceListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SlackAppWorkspaceListResponse]:
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
) -> Response[ErrorResponse | SlackAppWorkspaceListResponse]:
    """List Slack App Workspaces

     The Slack workspaces this pod's organization lets Lemma create apps in.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppWorkspaceListResponse]
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
) -> ErrorResponse | SlackAppWorkspaceListResponse | None:
    """List Slack App Workspaces

     The Slack workspaces this pod's organization lets Lemma create apps in.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppWorkspaceListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorResponse | SlackAppWorkspaceListResponse]:
    """List Slack App Workspaces

     The Slack workspaces this pod's organization lets Lemma create apps in.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppWorkspaceListResponse]
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
) -> ErrorResponse | SlackAppWorkspaceListResponse | None:
    """List Slack App Workspaces

     The Slack workspaces this pod's organization lets Lemma create apps in.

    Args:
        pod_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppWorkspaceListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
        )
    ).parsed

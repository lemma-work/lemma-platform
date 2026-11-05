from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.slack_app_workspace_response import SlackAppWorkspaceResponse
from ...models.slack_configuration_token_request import SlackConfigurationTokenRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: SlackConfigurationTokenRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/surface-setup/slack/app-creation".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | SlackAppWorkspaceResponse | None:
    if response.status_code == 200:
        response_200 = SlackAppWorkspaceResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | SlackAppWorkspaceResponse]:
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
    body: SlackConfigurationTokenRequest,
) -> Response[ErrorResponse | SlackAppWorkspaceResponse]:
    """Save Slack Configuration Token

     Let Lemma create Slack apps in the workspace this token is for.

    Args:
        pod_id (UUID):
        body (SlackConfigurationTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppWorkspaceResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SlackConfigurationTokenRequest,
) -> ErrorResponse | SlackAppWorkspaceResponse | None:
    """Save Slack Configuration Token

     Let Lemma create Slack apps in the workspace this token is for.

    Args:
        pod_id (UUID):
        body (SlackConfigurationTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppWorkspaceResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SlackConfigurationTokenRequest,
) -> Response[ErrorResponse | SlackAppWorkspaceResponse]:
    """Save Slack Configuration Token

     Let Lemma create Slack apps in the workspace this token is for.

    Args:
        pod_id (UUID):
        body (SlackConfigurationTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | SlackAppWorkspaceResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: SlackConfigurationTokenRequest,
) -> ErrorResponse | SlackAppWorkspaceResponse | None:
    """Save Slack Configuration Token

     Let Lemma create Slack apps in the workspace this token is for.

    Args:
        pod_id (UUID):
        body (SlackConfigurationTokenRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | SlackAppWorkspaceResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

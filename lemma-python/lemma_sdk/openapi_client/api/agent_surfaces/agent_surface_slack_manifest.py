from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_surface_slack_manifest_response_agent_surface_slack_manifest import (
    AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest,
)
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    agent_name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_agent_name: None | str | Unset
    if isinstance(agent_name, Unset):
        json_agent_name = UNSET
    else:
        json_agent_name = agent_name
    params["agent_name"] = json_agent_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/surface-setup/slack/manifest",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = (
            AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest.from_dict(
                response.json()
            )
        )

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
) -> Response[
    AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse
]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> Response[
    AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse
]:
    """Get Slack App Manifest

     The Slack app manifest to paste when running your own Slack app.

    Served rather than copied out of the repo so the URLs always match the
    deployment answering this request, and the scopes always match the code
    that will consume the events.

    Signed-in access is the only gate, and that is enough: every value in here
    is already public — this deployment's URLs and the scopes its own code
    asks for. It carries no credential and reveals nothing about a pod: the
    agent name is supplied by the caller and echoed back, never read from one.

    Args:
        agent_name (None | str | Unset): Name the app after this agent, so a bot made for one
            agent arrives already called by its name. Defaults to Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse]
    """

    kwargs = _get_kwargs(
        agent_name=agent_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse | None:
    """Get Slack App Manifest

     The Slack app manifest to paste when running your own Slack app.

    Served rather than copied out of the repo so the URLs always match the
    deployment answering this request, and the scopes always match the code
    that will consume the events.

    Signed-in access is the only gate, and that is enough: every value in here
    is already public — this deployment's URLs and the scopes its own code
    asks for. It carries no credential and reveals nothing about a pod: the
    agent name is supplied by the caller and echoed back, never read from one.

    Args:
        agent_name (None | str | Unset): Name the app after this agent, so a bot made for one
            agent arrives already called by its name. Defaults to Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse
    """

    return sync_detailed(
        client=client,
        agent_name=agent_name,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> Response[
    AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse
]:
    """Get Slack App Manifest

     The Slack app manifest to paste when running your own Slack app.

    Served rather than copied out of the repo so the URLs always match the
    deployment answering this request, and the scopes always match the code
    that will consume the events.

    Signed-in access is the only gate, and that is enough: every value in here
    is already public — this deployment's URLs and the scopes its own code
    asks for. It carries no credential and reveals nothing about a pod: the
    agent name is supplied by the caller and echoed back, never read from one.

    Args:
        agent_name (None | str | Unset): Name the app after this agent, so a bot made for one
            agent arrives already called by its name. Defaults to Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse]
    """

    kwargs = _get_kwargs(
        agent_name=agent_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    agent_name: None | str | Unset = UNSET,
) -> AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse | None:
    """Get Slack App Manifest

     The Slack app manifest to paste when running your own Slack app.

    Served rather than copied out of the repo so the URLs always match the
    deployment answering this request, and the scopes always match the code
    that will consume the events.

    Signed-in access is the only gate, and that is enough: every value in here
    is already public — this deployment's URLs and the scopes its own code
    asks for. It carries no credential and reveals nothing about a pod: the
    agent name is supplied by the caller and echoed back, never read from one.

    Args:
        agent_name (None | str | Unset): Name the app after this agent, so a bot made for one
            agent arrives already called by its name. Defaults to Lemma.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentSurfaceSlackManifestResponseAgentSurfaceSlackManifest | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            agent_name=agent_name,
        )
    ).parsed

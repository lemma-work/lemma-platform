from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_runtime_profile_response import AgentRuntimeProfileResponse
from ...models.error_response import ErrorResponse
from ...models.update_agent_host_runtime_profile_request import (
    UpdateAgentHostRuntimeProfileRequest,
)
from ...models.update_anthropic_compatible_runtime_profile_request import (
    UpdateAnthropicCompatibleRuntimeProfileRequest,
)
from ...models.update_open_ai_compatible_runtime_profile_request import (
    UpdateOpenAICompatibleRuntimeProfileRequest,
)
from ...types import Response


def _get_kwargs(
    organization_id: UUID,
    profile_id: str,
    *,
    body: UpdateAgentHostRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/organizations/{organization_id}/agent-runtime/profiles/{profile_id}".format(
            organization_id=quote(str(organization_id), safe=""),
            profile_id=quote(str(profile_id), safe=""),
        ),
    }

    if isinstance(body, UpdateAgentHostRuntimeProfileRequest):
        _kwargs["json"] = body.to_dict()
    elif isinstance(body, UpdateOpenAICompatibleRuntimeProfileRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentRuntimeProfileResponse.from_dict(response.json())

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
) -> Response[AgentRuntimeProfileResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    organization_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateAgentHostRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest,
) -> Response[AgentRuntimeProfileResponse | ErrorResponse]:
    """Update Agent Runtime Profile

    Args:
        organization_id (UUID):
        profile_id (str):
        body (UpdateAgentHostRuntimeProfileRequest |
            UpdateAnthropicCompatibleRuntimeProfileRequest |
            UpdateOpenAICompatibleRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        profile_id=profile_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    organization_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateAgentHostRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest,
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    """Update Agent Runtime Profile

    Args:
        organization_id (UUID):
        profile_id (str):
        body (UpdateAgentHostRuntimeProfileRequest |
            UpdateAnthropicCompatibleRuntimeProfileRequest |
            UpdateOpenAICompatibleRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileResponse | ErrorResponse
    """

    return sync_detailed(
        organization_id=organization_id,
        profile_id=profile_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    organization_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateAgentHostRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest,
) -> Response[AgentRuntimeProfileResponse | ErrorResponse]:
    """Update Agent Runtime Profile

    Args:
        organization_id (UUID):
        profile_id (str):
        body (UpdateAgentHostRuntimeProfileRequest |
            UpdateAnthropicCompatibleRuntimeProfileRequest |
            UpdateOpenAICompatibleRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        profile_id=profile_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    organization_id: UUID,
    profile_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdateAgentHostRuntimeProfileRequest
    | UpdateAnthropicCompatibleRuntimeProfileRequest
    | UpdateOpenAICompatibleRuntimeProfileRequest,
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    """Update Agent Runtime Profile

    Args:
        organization_id (UUID):
        profile_id (str):
        body (UpdateAgentHostRuntimeProfileRequest |
            UpdateAnthropicCompatibleRuntimeProfileRequest |
            UpdateOpenAICompatibleRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            organization_id=organization_id,
            profile_id=profile_id,
            client=client,
            body=body,
        )
    ).parsed

from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_runtime_profile_response import AgentRuntimeProfileResponse
from ...models.create_agent_host_runtime_profile_request import (
    CreateAgentHostRuntimeProfileRequest,
)
from ...models.create_anthropic_compatible_runtime_profile_request import (
    CreateAnthropicCompatibleRuntimeProfileRequest,
)
from ...models.create_open_ai_compatible_runtime_profile_request import (
    CreateOpenAICompatibleRuntimeProfileRequest,
)
from ...models.create_user_daemon_runtime_profile_request import (
    CreateUserDaemonRuntimeProfileRequest,
)
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    org_id: UUID,
    *,
    body: CreateAgentHostRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateUserDaemonRuntimeProfileRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/organizations/{org_id}/agent-runtime/profiles".format(
            org_id=quote(str(org_id), safe=""),
        ),
    }

    if isinstance(body, CreateUserDaemonRuntimeProfileRequest):
        _kwargs["json"] = body.to_dict()
    elif isinstance(body, CreateOpenAICompatibleRuntimeProfileRequest):
        _kwargs["json"] = body.to_dict()
    elif isinstance(body, CreateAnthropicCompatibleRuntimeProfileRequest):
        _kwargs["json"] = body.to_dict()
    else:
        _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    if response.status_code == 201:
        response_201 = AgentRuntimeProfileResponse.from_dict(response.json())

        return response_201

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
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentHostRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateUserDaemonRuntimeProfileRequest,
) -> Response[AgentRuntimeProfileResponse | ErrorResponse]:
    """Create Agent Runtime Profile

    Args:
        org_id (UUID):
        body (CreateAgentHostRuntimeProfileRequest |
            CreateAnthropicCompatibleRuntimeProfileRequest |
            CreateOpenAICompatibleRuntimeProfileRequest | CreateUserDaemonRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentHostRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateUserDaemonRuntimeProfileRequest,
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    """Create Agent Runtime Profile

    Args:
        org_id (UUID):
        body (CreateAgentHostRuntimeProfileRequest |
            CreateAnthropicCompatibleRuntimeProfileRequest |
            CreateOpenAICompatibleRuntimeProfileRequest | CreateUserDaemonRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileResponse | ErrorResponse
    """

    return sync_detailed(
        org_id=org_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentHostRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateUserDaemonRuntimeProfileRequest,
) -> Response[AgentRuntimeProfileResponse | ErrorResponse]:
    """Create Agent Runtime Profile

    Args:
        org_id (UUID):
        body (CreateAgentHostRuntimeProfileRequest |
            CreateAnthropicCompatibleRuntimeProfileRequest |
            CreateOpenAICompatibleRuntimeProfileRequest | CreateUserDaemonRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentRuntimeProfileResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    org_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: CreateAgentHostRuntimeProfileRequest
    | CreateAnthropicCompatibleRuntimeProfileRequest
    | CreateOpenAICompatibleRuntimeProfileRequest
    | CreateUserDaemonRuntimeProfileRequest,
) -> AgentRuntimeProfileResponse | ErrorResponse | None:
    """Create Agent Runtime Profile

    Args:
        org_id (UUID):
        body (CreateAgentHostRuntimeProfileRequest |
            CreateAnthropicCompatibleRuntimeProfileRequest |
            CreateOpenAICompatibleRuntimeProfileRequest | CreateUserDaemonRuntimeProfileRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentRuntimeProfileResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            org_id=org_id,
            client=client,
            body=body,
        )
    ).parsed

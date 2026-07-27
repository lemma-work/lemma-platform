from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.agent_host_poll_request import AgentHostPollRequest
from ...models.agent_host_poll_response import AgentHostPollResponse
from ...models.error_response import ErrorResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    body: AgentHostPollRequest,
    authorization: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/agent-host/v2/poll",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AgentHostPollResponse | ErrorResponse | None:
    if response.status_code == 200:
        response_200 = AgentHostPollResponse.from_dict(response.json())

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
) -> Response[AgentHostPollResponse | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AgentHostPollRequest,
    authorization: None | str | Unset = UNSET,
) -> Response[AgentHostPollResponse | ErrorResponse]:
    """Poll Agent Host Commands

    Args:
        authorization (None | str | Unset):
        body (AgentHostPollRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentHostPollResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        authorization=authorization,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    body: AgentHostPollRequest,
    authorization: None | str | Unset = UNSET,
) -> AgentHostPollResponse | ErrorResponse | None:
    """Poll Agent Host Commands

    Args:
        authorization (None | str | Unset):
        body (AgentHostPollRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentHostPollResponse | ErrorResponse
    """

    return sync_detailed(
        client=client,
        body=body,
        authorization=authorization,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    body: AgentHostPollRequest,
    authorization: None | str | Unset = UNSET,
) -> Response[AgentHostPollResponse | ErrorResponse]:
    """Poll Agent Host Commands

    Args:
        authorization (None | str | Unset):
        body (AgentHostPollRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AgentHostPollResponse | ErrorResponse]
    """

    kwargs = _get_kwargs(
        body=body,
        authorization=authorization,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    body: AgentHostPollRequest,
    authorization: None | str | Unset = UNSET,
) -> AgentHostPollResponse | ErrorResponse | None:
    """Poll Agent Host Commands

    Args:
        authorization (None | str | Unset):
        body (AgentHostPollRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AgentHostPollResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
            authorization=authorization,
        )
    ).parsed

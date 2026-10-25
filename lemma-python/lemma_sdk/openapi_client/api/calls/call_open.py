from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.call_response import CallResponse
from ...models.error_response import ErrorResponse
from ...models.open_call_request import OpenCallRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    *,
    body: OpenCallRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/pods/{pod_id}/calls".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> CallResponse | ErrorResponse | None:
    if response.status_code == 201:
        response_201 = CallResponse.from_dict(response.json())

        return response_201

    if response.status_code == 403:
        response_403 = ErrorResponse.from_dict(response.json())

        return response_403

    if response.status_code == 404:
        response_404 = ErrorResponse.from_dict(response.json())

        return response_404

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if response.status_code == 503:
        response_503 = ErrorResponse.from_dict(response.json())

        return response_503

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[CallResponse | ErrorResponse]:
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
    body: OpenCallRequest,
) -> Response[CallResponse | ErrorResponse]:
    """Open a call

     Open a live voice call with a pod agent. The call is a conversation: its agent coordinates the call,
    hands work to sub-agents and shows things on the call's canvas. Connect to
    `/pods/{pod_id}/calls/{call_id}/socket` to talk.

    Args:
        pod_id (UUID):
        body (OpenCallRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CallResponse | ErrorResponse]
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
    body: OpenCallRequest,
) -> CallResponse | ErrorResponse | None:
    """Open a call

     Open a live voice call with a pod agent. The call is a conversation: its agent coordinates the call,
    hands work to sub-agents and shows things on the call's canvas. Connect to
    `/pods/{pod_id}/calls/{call_id}/socket` to talk.

    Args:
        pod_id (UUID):
        body (OpenCallRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CallResponse | ErrorResponse
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
    body: OpenCallRequest,
) -> Response[CallResponse | ErrorResponse]:
    """Open a call

     Open a live voice call with a pod agent. The call is a conversation: its agent coordinates the call,
    hands work to sub-agents and shows things on the call's canvas. Connect to
    `/pods/{pod_id}/calls/{call_id}/socket` to talk.

    Args:
        pod_id (UUID):
        body (OpenCallRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CallResponse | ErrorResponse]
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
    body: OpenCallRequest,
) -> CallResponse | ErrorResponse | None:
    """Open a call

     Open a live voice call with a pod agent. The call is a conversation: its agent coordinates the call,
    hands work to sub-agents and shows things on the call's canvas. Connect to
    `/pods/{pod_id}/calls/{call_id}/socket` to talk.

    Args:
        pod_id (UUID):
        body (OpenCallRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CallResponse | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            body=body,
        )
    ).parsed

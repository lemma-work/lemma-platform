from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.connect_pod_request import ConnectPodRequest
from ...models.error_response import ErrorResponse
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    asking_pod_id: UUID,
    *,
    body: ConnectPodRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "put",
        "url": "/pods/{pod_id}/pod-links/{asking_pod_id}".format(
            pod_id=quote(str(pod_id), safe=""),
            asking_pod_id=quote(str(asking_pod_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ErrorResponse | None:
    if response.status_code == 204:
        response_204 = cast(Any, None)
        return response_204

    if response.status_code == 400:
        response_400 = ErrorResponse.from_dict(response.json())

        return response_400

    if response.status_code == 409:
        response_409 = ErrorResponse.from_dict(response.json())

        return response_409

    if response.status_code == 422:
        response_422 = ErrorResponse.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | ErrorResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    asking_pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ConnectPodRequest,
) -> Response[Any | ErrorResponse]:
    """Connect A Pod To This One

     Let another pod in this organization ask this one, sharing the tables and folders named for reading.
    Connecting again replaces what is shared. Needs pod.member.manage here, and membership of the other
    pod; the caller looks after the link.

    Args:
        pod_id (UUID):
        asking_pod_id (UUID):
        body (ConnectPodRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        asking_pod_id=asking_pod_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    asking_pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ConnectPodRequest,
) -> Any | ErrorResponse | None:
    """Connect A Pod To This One

     Let another pod in this organization ask this one, sharing the tables and folders named for reading.
    Connecting again replaces what is shared. Needs pod.member.manage here, and membership of the other
    pod; the caller looks after the link.

    Args:
        pod_id (UUID):
        asking_pod_id (UUID):
        body (ConnectPodRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        asking_pod_id=asking_pod_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    asking_pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ConnectPodRequest,
) -> Response[Any | ErrorResponse]:
    """Connect A Pod To This One

     Let another pod in this organization ask this one, sharing the tables and folders named for reading.
    Connecting again replaces what is shared. Needs pod.member.manage here, and membership of the other
    pod; the caller looks after the link.

    Args:
        pod_id (UUID):
        asking_pod_id (UUID):
        body (ConnectPodRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ErrorResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        asking_pod_id=asking_pod_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    asking_pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    body: ConnectPodRequest,
) -> Any | ErrorResponse | None:
    """Connect A Pod To This One

     Let another pod in this organization ask this one, sharing the tables and folders named for reading.
    Connecting again replaces what is shared. Needs pod.member.manage here, and membership of the other
    pod; the caller looks after the link.

    Args:
        pod_id (UUID):
        asking_pod_id (UUID):
        body (ConnectPodRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ErrorResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            asking_pod_id=asking_pod_id,
            client=client,
            body=body,
        )
    ).parsed

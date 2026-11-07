from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.machine_response import MachineResponse
from ...models.update_pod_machine_request import UpdatePodMachineRequest
from ...types import Response


def _get_kwargs(
    pod_id: UUID,
    name: str,
    *,
    body: UpdatePodMachineRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/pods/{pod_id}/machines/{name}".format(
            pod_id=quote(str(pod_id), safe=""),
            name=quote(str(name), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | MachineResponse | None:
    if response.status_code == 200:
        response_200 = MachineResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | MachineResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdatePodMachineRequest,
) -> Response[ErrorResponse | MachineResponse]:
    """Rename, resize or change who sees a pod machine

    Args:
        pod_id (UUID):
        name (str):
        body (UpdatePodMachineRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MachineResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        name=name,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdatePodMachineRequest,
) -> ErrorResponse | MachineResponse | None:
    """Rename, resize or change who sees a pod machine

    Args:
        pod_id (UUID):
        name (str):
        body (UpdatePodMachineRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MachineResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        name=name,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdatePodMachineRequest,
) -> Response[ErrorResponse | MachineResponse]:
    """Rename, resize or change who sees a pod machine

    Args:
        pod_id (UUID):
        name (str):
        body (UpdatePodMachineRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | MachineResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        name=name,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    name: str,
    *,
    client: AuthenticatedClient | Client,
    body: UpdatePodMachineRequest,
) -> ErrorResponse | MachineResponse | None:
    """Rename, resize or change who sees a pod machine

    Args:
        pod_id (UUID):
        name (str):
        body (UpdatePodMachineRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | MachineResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            name=name,
            client=client,
            body=body,
        )
    ).parsed

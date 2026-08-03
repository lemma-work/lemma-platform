from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.resource_access_invite_list_response import (
    ResourceAccessInviteListResponse,
)
from ...models.resource_type import ResourceType
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    resource_type: ResourceType,
    resource_id: None | Unset | UUID = UNSET,
    resource_name: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_resource_type = resource_type.value
    params["resource_type"] = json_resource_type

    json_resource_id: None | str | Unset
    if isinstance(resource_id, Unset):
        json_resource_id = UNSET
    elif isinstance(resource_id, UUID):
        json_resource_id = str(resource_id)
    else:
        json_resource_id = resource_id
    params["resource_id"] = json_resource_id

    json_resource_name: None | str | Unset
    if isinstance(resource_name, Unset):
        json_resource_name = UNSET
    else:
        json_resource_name = resource_name
    params["resource_name"] = json_resource_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/resource-access-invites".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ResourceAccessInviteListResponse | None:
    if response.status_code == 200:
        response_200 = ResourceAccessInviteListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ResourceAccessInviteListResponse]:
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
    resource_type: ResourceType,
    resource_id: None | Unset | UUID = UNSET,
    resource_name: None | str | Unset = UNSET,
) -> Response[ErrorResponse | ResourceAccessInviteListResponse]:
    """List Pending Invites for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (None | Unset | UUID):
        resource_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceAccessInviteListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    resource_type: ResourceType,
    resource_id: None | Unset | UUID = UNSET,
    resource_name: None | str | Unset = UNSET,
) -> ErrorResponse | ResourceAccessInviteListResponse | None:
    """List Pending Invites for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (None | Unset | UUID):
        resource_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceAccessInviteListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    resource_type: ResourceType,
    resource_id: None | Unset | UUID = UNSET,
    resource_name: None | str | Unset = UNSET,
) -> Response[ErrorResponse | ResourceAccessInviteListResponse]:
    """List Pending Invites for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (None | Unset | UUID):
        resource_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceAccessInviteListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    resource_type: ResourceType,
    resource_id: None | Unset | UUID = UNSET,
    resource_name: None | str | Unset = UNSET,
) -> ErrorResponse | ResourceAccessInviteListResponse | None:
    """List Pending Invites for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (None | Unset | UUID):
        resource_name (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceAccessInviteListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
        )
    ).parsed

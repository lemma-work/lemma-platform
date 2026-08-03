from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.resource_access_request_response import ResourceAccessRequestResponse
from ...models.resource_type import ResourceType
from ...types import UNSET, Response


def _get_kwargs(
    pod_id: UUID,
    *,
    resource_type: ResourceType,
    resource_id: UUID,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_resource_type = resource_type.value
    params["resource_type"] = json_resource_type

    json_resource_id = str(resource_id)
    params["resource_id"] = json_resource_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/resource-access-requests/me".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | None | ResourceAccessRequestResponse | None:
    if response.status_code == 200:

        def _parse_response_200(data: object) -> None | ResourceAccessRequestResponse:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                response_200_type_0 = ResourceAccessRequestResponse.from_dict(data)

                return response_200_type_0
            except TypeError, ValueError, AttributeError, KeyError:
                pass
            return cast(None | ResourceAccessRequestResponse, data)

        response_200 = _parse_response_200(response.json())

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
) -> Response[ErrorResponse | None | ResourceAccessRequestResponse]:
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
    resource_id: UUID,
) -> Response[ErrorResponse | None | ResourceAccessRequestResponse]:
    """Get My Pending Request for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | None | ResourceAccessRequestResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
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
    resource_id: UUID,
) -> ErrorResponse | None | ResourceAccessRequestResponse | None:
    """Get My Pending Request for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | None | ResourceAccessRequestResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        resource_type=resource_type,
        resource_id=resource_id,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    resource_type: ResourceType,
    resource_id: UUID,
) -> Response[ErrorResponse | None | ResourceAccessRequestResponse]:
    """Get My Pending Request for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | None | ResourceAccessRequestResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    resource_type: ResourceType,
    resource_id: UUID,
) -> ErrorResponse | None | ResourceAccessRequestResponse | None:
    """Get My Pending Request for a Resource

    Args:
        pod_id (UUID):
        resource_type (ResourceType):
        resource_id (UUID):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | None | ResourceAccessRequestResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    ).parsed

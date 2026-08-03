from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_response import ErrorResponse
from ...models.resource_access_request_list_response import (
    ResourceAccessRequestListResponse,
)
from ...models.resource_access_request_status import ResourceAccessRequestStatus
from ...types import UNSET, Response, Unset


def _get_kwargs(
    pod_id: UUID,
    *,
    request_status: None
    | ResourceAccessRequestStatus
    | Unset = ResourceAccessRequestStatus.PENDING,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_request_status: None | str | Unset
    if isinstance(request_status, Unset):
        json_request_status = UNSET
    elif isinstance(request_status, ResourceAccessRequestStatus):
        json_request_status = request_status.value
    else:
        json_request_status = request_status
    params["request_status"] = json_request_status

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/pods/{pod_id}/resource-access-requests".format(
            pod_id=quote(str(pod_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ErrorResponse | ResourceAccessRequestListResponse | None:
    if response.status_code == 200:
        response_200 = ResourceAccessRequestListResponse.from_dict(response.json())

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
) -> Response[ErrorResponse | ResourceAccessRequestListResponse]:
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
    request_status: None
    | ResourceAccessRequestStatus
    | Unset = ResourceAccessRequestStatus.PENDING,
) -> Response[ErrorResponse | ResourceAccessRequestListResponse]:
    """List Resource Access Requests

    Args:
        pod_id (UUID):
        request_status (None | ResourceAccessRequestStatus | Unset):  Default:
            ResourceAccessRequestStatus.PENDING.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceAccessRequestListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        request_status=request_status,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    request_status: None
    | ResourceAccessRequestStatus
    | Unset = ResourceAccessRequestStatus.PENDING,
) -> ErrorResponse | ResourceAccessRequestListResponse | None:
    """List Resource Access Requests

    Args:
        pod_id (UUID):
        request_status (None | ResourceAccessRequestStatus | Unset):  Default:
            ResourceAccessRequestStatus.PENDING.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceAccessRequestListResponse
    """

    return sync_detailed(
        pod_id=pod_id,
        client=client,
        request_status=request_status,
    ).parsed


async def asyncio_detailed(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    request_status: None
    | ResourceAccessRequestStatus
    | Unset = ResourceAccessRequestStatus.PENDING,
) -> Response[ErrorResponse | ResourceAccessRequestListResponse]:
    """List Resource Access Requests

    Args:
        pod_id (UUID):
        request_status (None | ResourceAccessRequestStatus | Unset):  Default:
            ResourceAccessRequestStatus.PENDING.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorResponse | ResourceAccessRequestListResponse]
    """

    kwargs = _get_kwargs(
        pod_id=pod_id,
        request_status=request_status,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    pod_id: UUID,
    *,
    client: AuthenticatedClient | Client,
    request_status: None
    | ResourceAccessRequestStatus
    | Unset = ResourceAccessRequestStatus.PENDING,
) -> ErrorResponse | ResourceAccessRequestListResponse | None:
    """List Resource Access Requests

    Args:
        pod_id (UUID):
        request_status (None | ResourceAccessRequestStatus | Unset):  Default:
            ResourceAccessRequestStatus.PENDING.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorResponse | ResourceAccessRequestListResponse
    """

    return (
        await asyncio_detailed(
            pod_id=pod_id,
            client=client,
            request_status=request_status,
        )
    ).parsed
